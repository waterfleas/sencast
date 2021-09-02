#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""The Identification of Pixel Properties (IdePix) algorithm classifies pixels from Sentinel-2, Sentinel-3, MERIS,
Landsat-8, MODIS, VIIRS, Proba-V, SPOT VGT amongst others in order to define surface types.

For an overview of the processor:
https://www.brockmann-consult.de/portfolio/idepix/

"""

import os
import subprocess

from utils.product_fun import get_reproject_params_from_wkt, get_main_file_from_product_path

# Key of the params section for this processor
PARAMS_SECTION = "LSWT"
# The name of the folder to which the output product will be saved
OUT_DIR = "LSWT"
# A pattern for the name of the file to which the output product will be saved (completed with product name)
OUT_FILENAME = "LSWT_{}.nc"
# The name of the xml file for gpt
GPT_XML_FILENAME = "lswt.xml"


def process(env, params, l1product_path, _, out_path):
    """ This processor applies the LSWT (Musenalp) Algorithm to the source products. """

    print("Applying LSWT...")
    gpt, product_name = env['General']['gpt_path'], os.path.basename(l1product_path)
    sensor, resolution, wkt = params['General']['sensor'], params['General']['resolution'], params['General']['wkt']

    output_file = os.path.join(out_path, OUT_DIR, OUT_FILENAME.format(product_name))
    if os.path.isfile(output_file):
        print("Skipping LSWT, target already exists: {}".format(os.path.basename(output_file)))
        return output_file
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    gpt_xml_file = os.path.join(out_path, OUT_DIR, "_reproducibility", GPT_XML_FILENAME.format(sensor))
    if not os.path.isfile(gpt_xml_file):
        rewrite_xml(gpt_xml_file, sensor, resolution, wkt)

    if sensor == "OLI_TIRS":
        l1product_path = get_main_file_from_product_path(l1product_path)
    args = [gpt, gpt_xml_file, "-c", env['General']['gpt_cache_size'], "-e", "-SsourceFile={}".format(l1product_path),
            "-PoutputFile={}".format(output_file)]
    print("Calling '{}'".format(args))
    if subprocess.call(args):
        if os.path.exists(output_file):
            os.remove(output_file)
        else:
            print("No file was created.")
        raise RuntimeError("GPT Failed.")

    return output_file


def rewrite_xml(gpt_xml_file, sensor, sattype, height):
    with open(os.path.join(os.path.dirname(__file__), GPT_XML_FILENAME.format(sensor)), "r") as f:
        xml = f.read()

    xml = xml.replace("${sattype}", sattype)
    xml = xml.replace("${height}", height)

    os.makedirs(os.path.dirname(gpt_xml_file), exist_ok=True)
    with open(gpt_xml_file, "w") as f:
        f.write(xml)
