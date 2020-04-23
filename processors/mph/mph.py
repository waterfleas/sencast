#! /usr/bin/env python
# -*- coding: utf-8 -*-

import os
import subprocess

from packages.ql_mapping import plot_map

# Key of the params section for this processor
PARAMS_SECTION = "MPH"
# The name of the folder to which the output product will be saved
OUT_DIR = "L2MPH"
# A pattern for the name of the file to which the output product will be saved (completed with product name)
OUT_FILENAME = "L2MPH_L1P_reproj_{}.nc"
# A pattern for name of the folder to which the quicklooks will be saved (completed with band name)
QL_DIR = "L2MPH-{}"
# A pattern for the name of the file to which the quicklooks will be saved (completed with product name and band name)
QL_FILENAME = "L2MPH_L1P_reproj_{}_{}.png"
# The name of the xml file for gpt
GPT_XML_FILENAME = "mph.xml"


def process(env, params, l1_product_path, source_file, out_path):
    """ This processor applies mph to the source product and stores the result. """

    print("Applying MPH...")
    gpt, product_name = env['General']['gpt_path'], os.path.basename(l1_product_path)
    sensor, resolution, wkt = params['General']['sensor'], params['General']['resolution'], params['General']['wkt']
    validexpression = params[PARAMS_SECTION]['validexpression']

    if sensor != "OLCI":
        return

    output_file = os.path.join(out_path, OUT_DIR, OUT_FILENAME.format(product_name))
    if os.path.isfile(output_file):
        print("Skipping MPH, target already exists: {}".format(OUT_FILENAME.format(product_name)))
        return output_file
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    gpt_xml_file = os.path.join(out_path, OUT_DIR, "_reproducibility", GPT_XML_FILENAME)
    if not os.path.isfile(gpt_xml_file):
        rewrite_xml(gpt_xml_file, validexpression)

    args = [gpt, gpt_xml_file, "-c", env['General']['gpt_cache_size'], "-e", "-SsourceFile={}".format(source_file),
            "-PoutputFile={}".format(output_file)]
    if subprocess.call(args):
        raise RuntimeError("GPT Failed.")

    create_quicklooks(params, output_file, product_name, out_path, wkt)

    return output_file


def rewrite_xml(gpt_xml_file, validexpression):
    with open(os.path.join(os.path.dirname(__file__), GPT_XML_FILENAME), "r") as f:
        xml = f.read()

    xml = xml.replace("${validPixelExpression}", validexpression)
    xml = xml.replace("${cyanoMaxValue}", str(1000.0))
    xml = xml.replace("${chlThreshForFloatFlag}", str(500.0))
    xml = xml.replace("${exportMph}", "true")
    xml = xml.replace("${applyLowPassFilter}", "false")

    os.makedirs(os.path.dirname(gpt_xml_file), exist_ok=True)
    with open(gpt_xml_file, "w") as f:
        f.write(xml)


def create_quicklooks(params, product_file, product_name, out_path, wkt):
    bands, bandmaxs = [list(filter(None, params[PARAMS_SECTION][key].split(","))) for key in ['bands', 'bandmaxs']]
    print("Creating quicklooks for MPH for bands: {}".format(bands))
    for band, bandmax in zip(bands, bandmaxs):
        bandmax = False if int(bandmax) == 0 else range(0, int(bandmax))
        ql_file = os.path.join(out_path, QL_DIR.format(band), QL_FILENAME.format(product_name, band))
        os.makedirs(os.path.dirname(ql_file), exist_ok=True)
        plot_map(product_file, ql_file, band, wkt, basemap="srtm_hillshade", param_range=bandmax)
