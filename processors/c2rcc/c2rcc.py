#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""The Case-2 Regional CoastColour (C2RCC) algorithm derives the water constituents and optical properties from
optically complex waters using Sentinels–3 and 2, MERIS, VIIRS, MODIS, or Landsat-8 images.

For an overview of the processor:
https://www.brockmann-consult.de/portfolio/water-quality-from-space/

or for more details:
https://www.brockmann-consult.de/wp-content/uploads/2017/11/sco1_12brockmann.pdf
"""

import os
import subprocess

from datetime import datetime
from polymer.ancillary_era5 import Ancillary_ERA5
from utils.auxil import load_properties, log, gpt_subprocess
from utils.product_fun import get_lons_lats, get_sensing_date_from_product_name

# Key of the params section for this processor
PARAMS_SECTION = "C2RCC"
# The name of the folder to which the output product will be saved
OUT_DIR = "L2C2RCC"
# A pattern for the name of the file to which the output product will be saved (completed with product name)
OUT_FILENAME = "L2C2RCC_{}_{}.nc"
# A pattern for name of the folder to which the quicklooks will be saved (completed with band name)
QL_DIR = "L2C2RCC-{}"
# A pattern for the name of the file to which the quicklooks will be saved (completed with product name and band name)
QL_FILENAME = "L2C2RCC_{}_{}.png"
# The name of the xml file for gpt
GPT_XML_FILENAME = "c2rcc_{}_{}.xml"
# Default number of attempts for the GPT
DEFAULT_ATTEMPTS = 1
# Default timeout for the GPT (doesn't apply to last attempt) in seconds
DEFAULT_TIMEOUT = False


def process(env, params, l1product_path, l2product_files, out_path):
    """This processor applies c2rcc to the source product and stores the result."""

    gpt, product_name = env['General']['gpt_path'], os.path.basename(l1product_path)
    sensor, resolution, wkt, resolution = params['General']['sensor'], params['General']['resolution'], params['General']['wkt'], params['General']['resolution']
    altnn, validexpression = params[PARAMS_SECTION]['altnn'], params[PARAMS_SECTION]['validexpression']
    vicar_properties_filename = params[PARAMS_SECTION]['vicar_properties_filename']
    date_str = get_sensing_date_from_product_name(product_name)

    ancillary_obj = {"ozone": "330", "surf_press": "1000", "useEcmwfAuxData": "False"}
    anc_name = "NA"
    if params['C2RCC']['ancillary_data'] == 'ERA5':
        ancillary_path = env['CDS']['era5_path']
        try:
            os.makedirs(ancillary_path, exist_ok=True)
            ancillary = Ancillary_ERA5(ancillary_path)
            date = datetime.strptime(date_str, "%Y%m%dT%H%M%S")
            lons, lats = get_lons_lats(wkt)
            coords = (max(lats) + min(lats)) / 2, (max(lons) + min(lons)) / 2
            ozone = round(ancillary.get("ozone", date)[coords])
            surf_press = round(ancillary.get("surf_press", date)[coords])
            ancillary_obj = {"ozone": ozone, "surf_press": surf_press, "useEcmwfAuxData": "False"}
            anc_name = "ERA5"
            log(env["General"]["log"],
                "C2RCC Ancillary Data successfully retrieved. Ozone: {}, Surface Pressure {}".format(ozone, surf_press))
        except RuntimeError:
            log(env["General"]["log"], "C2RCC Ancillary Data not retrieved using default values. Ozone: 330, Surface Pressure 1000")
            if ancillary_path.endwith("METEO"):
                ancillary_obj["useEcmwfAuxData"] = "True"
            pass

    output_file = os.path.join(out_path, OUT_DIR, OUT_FILENAME.format(anc_name, product_name))
    if os.path.isfile(output_file):
        if "synchronise" in params["General"].keys() and params['General']['synchronise'] == "false":
            log(env["General"]["log"], "Removing file: ${}".format(output_file))
            os.remove(output_file)
        else:
            log(env["General"]["log"], "Skipping C2RCC, target already exists: {}".format(OUT_FILENAME.format(anc_name, product_name)))
            return output_file
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    if "processor" in params[PARAMS_SECTION]:
        if params[PARAMS_SECTION]["processor"] == "IDEPIX" and "IDEPIX" in l2product_files:
            log(env["General"]["log"], "Using IDEPIX as input file.", indent=1)
            input_file = l2product_files['IDEPIX']
        elif params[PARAMS_SECTION]["processor"] == "S2RES" and "S2RES" in l2product_files:
            log(env["General"]["log"], "Using S2RES as input file.", indent=1)
            input_file = l2product_files['S2RES']
        else:
            if params[PARAMS_SECTION]["processor"] in ["IDEPIX", "S2RES"]:
                raise RuntimeWarning('Processor {} was not found in l2 products. Ensure this processor is run before C2RCC'.format(params[PARAMS_SECTION]["processor"]))
            else:
                raise RuntimeWarning(
                    'Processor {} is not recognised. Please choose from IDEPIX and S2RES'.format(
                        params[PARAMS_SECTION]["processor"]))
    else:
        log(env["General"]["log"], "Using L1 product as input file.", indent=1)
        input_file = l1product_path
        if sensor == "MSI":
            sensor = "MSI_RES"

    gpt_xml_file = os.path.join(out_path, OUT_DIR, "_reproducibility", GPT_XML_FILENAME.format(sensor, date_str))
    rewrite_xml(gpt_xml_file, date_str, sensor, altnn, validexpression, vicar_properties_filename, wkt, ancillary_obj, resolution)

    if "gpt_use_default" in env['General'] and env['General']['gpt_use_default'] == "True":
        args = [gpt, gpt_xml_file, "-SsourceFile={}".format(input_file), "-PoutputFile={}".format(output_file)]
    else:
        args = [gpt, gpt_xml_file, "-c", env['General']['gpt_cache_size'], "-e",
                "-SsourceFile={}".format(input_file), "-PoutputFile={}".format(output_file)]

    if PARAMS_SECTION in params and "attempts" in params[PARAMS_SECTION]:
        attempts = int(params[PARAMS_SECTION]["attempts"])
    else:
        attempts = DEFAULT_ATTEMPTS

    if PARAMS_SECTION in params and "timeout" in params[PARAMS_SECTION]:
        timeout = int(params[PARAMS_SECTION]["timeout"])
    else:
        timeout = DEFAULT_TIMEOUT

    if gpt_subprocess(args, env["General"]["log"], attempts=attempts, timeout=timeout):
        return output_file
    else:
        if os.path.exists(output_file):
            os.remove(output_file)
            log(env["General"]["log"], "Removed corrupted output file.", indent=2)
        raise RuntimeError("GPT Failed.")

    return output_file


def rewrite_xml(gpt_xml_file, date_str, sensor, altnn, validexpression, vicar_properties_filename, wkt, ancillary, resolution):
    with open(os.path.join(os.path.dirname(__file__), GPT_XML_FILENAME.format(sensor, "")), "r") as f:
        xml = f.read()
    altnn_path = os.path.join(os.path.dirname(__file__), "altnn", altnn) if altnn else ""

    xml = xml.replace("${ozone}", ancillary["ozone"])
    xml = xml.replace("${resolution}", resolution)
    xml = xml.replace("${press}", ancillary["surf_press"])
    xml = xml.replace("${useEcmwfAuxData}", ancillary["useEcmwfAuxData"])
    xml = xml.replace("${validPixelExpression}", validexpression)
    xml = xml.replace("${salinity}", str(0.05))
    xml = xml.replace("${temperature}", str(15.0))
    xml = xml.replace("${TSMfakBpart}", str(1.72))
    xml = xml.replace("${TSMfakBwit}", str(3.1))
    xml = xml.replace("${CHLexp}", str(1.04))
    xml = xml.replace("${CHLfak}", str(21.0))
    xml = xml.replace("${thresholdRtosaOOS}", str(0.05))
    xml = xml.replace("${thresholdAcReflecOos}", str(0.1))
    xml = xml.replace("${thresholdCloudTDown865}", str(0.955))
    xml = xml.replace("${alternativeNNPath}", altnn_path)

    if vicar_properties_filename:
        vicar_properties_file = os.path.join(os.path.dirname(__file__), "vicarious", vicar_properties_filename)
        vicar_params = load_properties(vicar_properties_file)
        for key in vicar_params.keys():
            xml = xml.replace("${" + key + "}", vicar_params[key])

    os.makedirs(os.path.dirname(gpt_xml_file), exist_ok=True)
    with open(gpt_xml_file, "w") as f:
        f.write(xml)
