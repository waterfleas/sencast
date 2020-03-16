#! /usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import getpass
import concurrent.futures
import requests
from requests.auth import HTTPBasicAuth

from subprocess import check_output
from packages.auxil import list_xml_scene_dir
from zipfile import ZipFile

import xml.etree.ElementTree as ElementTree
import numpy as np


def prepend_ns(s):
    return '{http://www.w3.org/2005/Atom}' + s


def prepend_os(s):
    return '{http://a9.com/-/spec/opensearch/1.1/}' + s


def parse_coah_xml(filename):
    coah_xml = {}
    pnames = []
    uuids = []
    tree = ElementTree.parse(filename)
    root = tree.getroot()

    for titl in root.iter(prepend_ns('title')):
        if 'instrumentshortname' not in titl.text:
            pnames.append(titl.text)
    total_results = -1
    for tr in root.iter(prepend_os('totalResults')):
        total_results = int(tr.text)
    elems = [el for el in root.iter(prepend_ns('str'))]
    c = 0
    for el in elems:
        if el.get('name') == 'uuid':
            uuids.append(el.text)
            c += 1
    coah_xml['pnames'] = pnames
    coah_xml['uuids'] = uuids
    coah_xml['total_results'] = total_results
    coah_xml['nb_results'] = c
    return coah_xml


def coah_xmlparsed_to_txt(uuids, out_fname):
    basestr = 'https://scihub.copernicus.eu/dhus/odata/v1/Products(\'{}\')/$value\n'
    with open(out_fname, 'w+') as of:
        for uuid in uuids:
            of.write(basestr.format(uuid))


def wc(filename):
    return int(check_output(["wc", "-l", filename]).split()[0])


def download(url, usr, pwd, count):
    sys.stdout.write("\033[K")
    dl_name = url.split("'")[1]

    response = requests.get(url, auth=HTTPBasicAuth(usr, pwd), stream=True)
    with open(dl_name + '.zip', 'wb') as down_stream:
        for chunk in response.iter_content(chunk_size=65536):
            down_stream.write(chunk)
    with ZipFile(dl_name + '.zip', 'r') as zip_file:
        prod_name = zip_file.namelist()[0]
        zip_file.extractall(prod_name.split('.')[0])
    os.remove(dl_name + '.zip')
    print("Product no. {} downloaded".format(count), end="\r")


def query_dl_coah(params, outdir):
    xmlf = []
    wd = os.getcwd()
    if params['sensor'].upper() == 'OLCI' and params['resolution'].upper() == '1000':
        datatype = 'OL_1_ERR___'
    elif params['sensor'].upper() == 'OLCI' and params['resolution'].upper() != '1000':
        datatype = 'OL_1_EFR___'
    elif params['sensor'].upper() == 'MSI':
        datatype = 'S2MSI1C'
    else:
        raise RuntimeError("Unknown sensor: {}".format(params["sensor"]))

    # Send a product query
    query_url = 'https://scihub.copernicus.eu/dhus/search?q=instrumentshortname:' + \
                params['sensor'].lower() + \
                ' AND producttype:' + datatype + \
                ' AND beginPosition:[' + params['start'] + \
                ' TO ' + params['end'] + \
                '] AND footprint:"Intersects(' + params['wkt'] + \
                ')"&rows=100&start=0\''
    basic_auth = HTTPBasicAuth(params['username'], params['password'])
    response = requests.get(query_url.replace(' ', '+'), auth=basic_auth)
    xml_file = open('query-list.xml', 'wb')
    xml_file.write(response.text.encode())
    xml_file.close()
    try:
        coah_xml = parse_coah_xml('query-list.xml')
        os.remove('query-list.xml')
    except TypeError:
        print('No products found for this request. Exiting...')
        os.remove('query-list.xml')
        return
    total_results = coah_xml['total_results']
    print('{} products found'.format(total_results))

    # Download in junks of no more than 100 results at once
    nit = np.ceil(total_results / 100).astype(int)
    if nit == 1:
        all_pnames = coah_xml['pnames']
        all_uuids = coah_xml['uuids']
    else:
        all_pnames = []
        all_uuids = []
        c = 0
        for i in range(nit):
            product_url = 'https://scihub.copernicus.eu/dhus/search?q=instrumentshortname:' + \
                          params['sensor'].lower() + \
                          ' AND producttype:' + datatype + \
                          ' AND beginPosition:[' + params['start'] + \
                          ' TO ' + params['end'] + \
                          '] AND footprint:"Intersects(' + params['wkt'] + \
                          ')"&start=' + str(c) + '&rows=100&\''
            response = requests.get(product_url.replace(' ', '+'), auth=basic_auth)
            xml_file = open('products-list.xml', 'wb')
            xml_file.write(response.text.encode())
            xml_file.close()
            coah_xml = parse_coah_xml('products-list.xml')
            for pname in coah_xml['pnames']:
                all_pnames.append(pname)
            os.remove('products-list.xml')
            for uuid in coah_xml['uuids']:
                all_uuids.append(uuid)
            c += 100

    # Remove uuids already downloaded
    uuids, pnames = [], []
    for uuid, pn in zip(all_uuids, all_pnames):
        if pn.split('.')[0] not in os.listdir(outdir):
            if pn.split('.')[0] not in pnames:
                uuids.append(uuid)
                pnames.append(pn)
    # Download
    if uuids:
        user = getpass.getuser()
        url_list = os.path.join(outdir, 'urls_list_' + user + '.txt')
        if os.path.isfile(url_list):
            os.remove(url_list)
        coah_xmlparsed_to_txt(uuids, url_list)
        os.chdir(outdir)
        num_lines = sum(1 for _ in open(url_list))
        print(str(num_lines) + ' missing in input_data folder, starting download')
        with open(url_list) as f:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                for i_line, line in enumerate(f):
                    line = line.rstrip('\n')
                    ex.submit(download, line, params['username'], params['password'], str(i_line + 1))

        # Go back to working directory
        os.chdir(wd)

        # Check if products were actually dowloaded:
        lsdir = [lsd.split('.')[0] for lsd in os.listdir(outdir)]
        c = 0
        for pn in pnames:
            if pn.split('.')[0] in lsdir:
                c += 1
        if c != len(pnames):
            print('\nDownload(s) failed, another user might be using COAH services with the same credentials.' +
                  ' Either wait for the other user to finish their job or change the credentials in the parameter file.')
            os.remove(url_list)
            return
        else:
            print('\nDownload complete')
            os.remove(url_list)
    else:
        print('All products already downloaded, skipping...')

    if total_results > 0:
        # Read products
        xmlf = list_xml_scene_dir(outdir, sensor=params['sensor'], file_list=all_pnames)
    return xmlf
