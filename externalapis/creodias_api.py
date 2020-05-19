#! /usr/bin/env python
# -*- coding: utf-8 -*-

import os
import requests

from requests.status_codes import codes
from xml.etree import ElementTree
from zipfile import ZipFile

# Documentation for CREODIAS API can be found here:
# https://creodias.eu/eo-data-finder-api-manual

# search address
search_address = "https://finder.creodias.eu/resto/api/collections/Sentinel3/search.json?{}"

# download address
download_address = "https://zipper.creodias.eu/download/{}?token={}"

# token address
token_address = 'https://auth.creodias.eu/auth/realms/DIAS/protocol/openid-connect/token'


def get_download_requests(auth, startDate, completionDate, sensor, resolution, wkt):
    query = "maxRecords={}&startDate={}&completionDate={}&instrument={}&geometry={}&productType={}"
    maxRecords = 100
    geometry = wkt.replace(" ", "", 1).replace(" ", "+")
    instrument, productType = get_dataset_id(sensor, resolution)
    query = query.format(maxRecords, startDate, completionDate, instrument, geometry, productType)
    uuids, product_names, timelinesss, beginpositions, endpositions = search(query)
    uuids, product_names = timeliness_filter(uuids, product_names, timelinesss, beginpositions, endpositions)
    return [{'uuid': uuid} for uuid in uuids], product_names

def timeliness_filter(uuids, product_names, timelinesss, beginpositions, endpositions):
    num_products = len(uuids)
    uuids_filtered, product_names_filtered, positions, timelinesss_filtered = [], [], [], []
    for i in range(num_products):
        curr_pos = (beginpositions[i], endpositions[i])
        if curr_pos in positions:
            curr_proj_idx = positions.index(curr_pos)
            if timelinesss[i] == 'Non Time Critical' and timelinesss_filtered[curr_proj_idx] == 'Near Real Time':
                timelinesss_filtered[curr_proj_idx] = timelinesss[i]
                uuids_filtered[curr_proj_idx] = uuids[i]
                product_names_filtered[curr_proj_idx] = product_names[i]
                positions[curr_proj_idx] = (beginpositions[i], endpositions[i])
            elif timelinesss[i] == 'Near Real Time' and timelinesss_filtered[curr_proj_idx] == 'Non Time Critical':
                continue
            else:
                timelinesss_filtered.append(timelinesss[i])
                uuids_filtered.append(uuids[i])
                product_names_filtered.append(product_names[i])
                positions.append((beginpositions[i], endpositions[i]))
        else:
            timelinesss_filtered.append(timelinesss[i])
            uuids_filtered.append(uuids[i])
            product_names_filtered.append(product_names[i])
            positions.append((beginpositions[i], endpositions[i]))
    return uuids_filtered, product_names_filtered


def do_download(auth, download_request, product_path):
    download(auth, download_request['uuid'], product_path)


def get_dataset_id(sensor, resolution):
    if sensor == 'OLCI' and int(resolution) < 1000:
        return 'OL', 'EFR'
    elif sensor == 'OLCI' and int(resolution) >= 1000:
        return 'OL', 'ERR'
    else:
        raise RuntimeError("CREODIAS API is not yet implemented for sensor: {}".format(sensor))


def search(query):
    print("Search for products: {}".format(query))
    uuids, filenames = [], []
    timelinesss, beginpositions, endpositions = [], [], []
    while True:
        response = requests.get(search_address.format(query))
        if response.status_code == codes.OK:
            root = response.json()
            for feature in root['features']:
                uuids.append(feature['id'])
                filenames.append(feature['properties']['title'])
                timelinesss.append(feature['properties']['timeliness'])
                beginpositions.append(feature['properties']['startDate'])
                endpositions.append(feature['properties']['completionDate'])
            return uuids, filenames, timelinesss, beginpositions, endpositions
        else:
            raise RuntimeError("Unexpected response: {}".format(response.text))


def download(auth, uuid, filename):
    username = auth[0]
    password = auth[1]
    token = get_token(username, password)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    response = requests.get(download_address.format(uuid, token), stream=True)
    if response.status_code == codes.OK:
        with open(filename + '.zip', 'wb') as down_stream:
            for chunk in response.iter_content(chunk_size=65536):
                down_stream.write(chunk)
        with ZipFile(filename + '.zip', 'r') as zip_file:
            zip_file.extractall(os.path.dirname(filename))
        os.remove(filename + '.zip')
    else:
        print("Unexpected response on download request: {}".format(response.text))


def get_token(username, password):
    token_data = {
        'client_id': 'CLOUDFERRO_PUBLIC',
        'username': username,
        'password': password,
        'grant_type': 'password'
    }
    response = requests.post(token_address, data=token_data).json()
    try:
        return response['access_token']
    except KeyError:
        raise RuntimeError(f'Unable to get token. Response was {response}')