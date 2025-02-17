#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""This module bundles utility functions regarding satellite products."""

import numpy as np
import os
import re
import subprocess
from math import ceil, floor

import pandas as pd
from haversine import haversine
from datetime import datetime

from netCDF4 import Dataset

from utils.auxil import log


def parse_s3_name(name):
    if "S3A_" in name or "S3B_" in name:
        name_list = name.split("_")
        return name_list[7], name_list[8], name_list[9], name_list[0]
    else:
        return False, False, False, False


def parse_date_from_name(name):
    sensing_time = name.split("_")[7]
    sensing_year = sensing_time[:4]
    sensing_month = sensing_time[4:6]
    sensing_day = sensing_time[6:8]
    creation_time = datetime.strptime(name.split("_")[9], '%Y%m%dT%H%M%S')
    return "{}-{}-{}".format(sensing_year, sensing_month, sensing_day), creation_time


def get_satellite_name_from_product_name(product_name):
    """Return the satellite name of a given product name."""
    if "S3A" in product_name:
        return "S3A"
    elif "S3B" in product_name:
        return "S3B"
    elif "S2A" in product_name:
        return "S2A"
    elif "S2B" in product_name:
        return "S2B"
    elif "LC08" in product_name:
        return "L8"
    raise RuntimeError("Could not read satellite name from product name [{}]".format(product_name))


def filter_for_baseline(download_requests, product_names, sensor, env):
    filtered_download_requests = []
    filtered_product_names = []
    if sensor == "MSI":
        log(env["General"]["log"], "Filtering for most recent baseline", indent=1)
        data = []
        for i in range(len(download_requests)):
            p = product_names[i].split("_")
            data.append([p[0] + p[1] + p[2] + p[4] + p[5], p[3], i])
        df = pd.DataFrame(data, columns=["id", "baseline", "index"])
        df = df.sort_values(by=['id', 'baseline'], ascending=False)
        df = df.groupby('id').first()
        for i in df["index"].values:
            filtered_download_requests.append(download_requests[i])
            filtered_product_names.append(product_names[i])
        return filtered_download_requests, filtered_product_names
    else:
        return download_requests, product_names


def filter_for_tiles(download_requests, product_names, tiles, env):
    log(env["General"]["log"], "Filtering to only include the following tiles: {}.".format(", ".join(tiles)), indent=1)
    filtered_download_requests = []
    filtered_product_names = []
    for i in range(len(download_requests)):
        for tile in tiles:
            if "_{}_".format(tile) in product_names[i]:
                filtered_download_requests.append(download_requests[i])
                filtered_product_names.append(product_names[i])
    return filtered_download_requests, filtered_product_names


def filter_for_timeliness(download_requests, product_names, env):
    s3_products = []
    for i in range(len(product_names)):
        tmp = product_names[i]
        uuid = download_requests[i]["uuid"]
        if "S3A_" in tmp or "S3B_" in tmp:
            sensing_start, sensing_end, product_creation, satellite = parse_s3_name(tmp)
            s3_products.append({"name": tmp, "uuid": uuid, "sensing_start": sensing_start, "sensing_end": sensing_end,
                                "product_creation": product_creation, "satellite": satellite})
        else:
            s3_products.append({"name": tmp, "uuid": uuid})
    filtered_download_requests = []
    filtered_product_names = []
    for j in range(len(s3_products)):
        if "S3A_" in s3_products[j]["name"] or "S3B_" in s3_products[j]["name"]:
            matching_sensing = [f for f in s3_products if f['sensing_start'] == s3_products[j]['sensing_start']
                                and f['sensing_end'] == s3_products[j]['sensing_end']
                                and f['satellite'] == s3_products[j]['satellite']]
            creation = [d['product_creation'] for d in matching_sensing]
            creation.sort(reverse=True)
            if s3_products[j]['product_creation'] == creation[0]:
                filtered_product_names.append(s3_products[j]["name"])
                filtered_download_requests.append({"uuid": s3_products[j]["uuid"]})
            else:
                log(env["General"]["log"], "Removed superseded file: {}).".format(s3_products[j]["name"]))
        else:
            filtered_product_names.append(s3_products[j]["name"])
            filtered_download_requests.append({"uuid": s3_products[j]["uuid"]})

    return filtered_download_requests, filtered_product_names


def get_south_east_north_west_bound(wkt):
    """Return south, east, north, and west boundery of a given wkt."""
    lons, lats = get_lons_lats(wkt)
    return min(lats), max(lons), max(lats), min(lons)


def get_lons_lats(wkt):
    """ Return one array with all longitudes and one array with all latitudes of the perimeter corners. """
    if wkt[0:7].lower() != "polygon":
        raise RuntimeError("Provided wkt must be a polygon!")
    corners = [float(c) for c in re.findall(r'[-]?\d+\.\d+', wkt)]
    lons = [float(corner) for corner in corners[::2]]
    lats = [float(corner) for corner in corners[1::2]]
    return lons, lats


def get_reproject_params_from_wkt(wkt, resolution):
    """Calculates reprojection parameters from a given wkt."""
    south, east, north, west = get_south_east_north_west_bound(wkt)
    x_dist = haversine((south, west), (south, east))
    y_dist = haversine((south, west), (north, west))
    x_pix = int(round(x_dist / (int(resolution) / 1000)))
    y_pix = int(round(y_dist / (int(resolution) / 1000)))
    x_pixsize = (east - west) / x_pix
    y_pixsize = (north - south) / y_pix

    return {'easting': str(west), 'northing': str(north), 'pixelSizeX': str(x_pixsize),
            'pixelSizeY': str(y_pixsize), 'width': str(x_pix), 'height': str(y_pix)}


def get_sensing_date_from_product_name(product_name):
    """Read the sensing date from a product name."""
    return re.findall(r"\d{8}", product_name)[0]


def get_sensing_datetime_from_product_name(product_name):
    return re.findall(r"\d{8}", product_name)[0] + "T" + re.findall(r"\d{6}", product_name)[1]


def get_l1product_path(env, product_name):
    """Fills the placeholders in the configured DIAS path with actual values."""
    if product_name.startswith("S3A") or product_name.startswith("S3B"):
        satellite = "Sentinel-3"
        sensor = "OLCI"
        dataset = product_name[4:12]
        date = datetime.strptime(get_sensing_date_from_product_name(product_name), r"%Y%m%d")
    elif product_name.startswith("S2A") or product_name.startswith("S2B"):
        satellite = "Sentinel-2"
        sensor = "MSI"
        dataset = product_name[7:10]
        date = datetime.strptime(get_sensing_date_from_product_name(product_name), r"%Y%m%d")
    elif product_name.startswith("LC08"):
        satellite = "Landsat8"
        sensor = "OLI_TIRS"
        dataset = product_name[5:9]
        date = datetime.strptime(get_sensing_date_from_product_name(product_name), r"%Y%m%d")
    else:
        raise RuntimeError("Unable to retrieve satellite from product name: {}".format(product_name))

    kwargs = {
        'product_name': product_name,
        'satellite': satellite,
        'sensor': sensor,
        'dataset': dataset,
        'year': date.strftime(r"%Y"),
        'month': date.strftime(r"%m"),
        'day': date.strftime(r"%d")
    }

    return env['DIAS']['l1_path'].format(**kwargs)


def get_main_file_from_product_path(l1product_path):
    """Returns the path to the file to be read by third-party software in order to open a L1 product."""
    product_name = os.path.basename(l1product_path)
    satellite = get_satellite_name_from_product_name(product_name)
    if satellite in ["S2A", "S2B"]:
        return os.path.join(l1product_path, "MTD_MSIL1C.xml")
    elif satellite in ["S3A", "S3B"]:
        return os.path.join(l1product_path, "xfdumanifest.xml")
    elif satellite == "L8":
        return os.path.join(l1product_path, "{}_MTL.txt".format(product_name))
    else:
        raise RuntimeError("Unknown satellite: {}".format(satellite))


def generate_l8_angle_files(env, l1product_path):
    """Generates angle files for Landsat 8 L1 products."""
    if env['DIAS']['readonly'] == "True":
        raise RuntimeError("Cannot generate L8 angle files on read-only DIAS.")
    product_name = os.path.basename(l1product_path)
    ang_file = os.path.join(l1product_path, "{}_ANG.txt".format(product_name))
    args = [os.path.join(env['L8_ANGLES']['root_path'], "l8_angles"), ang_file, "BOTH", "1", "-b", "1"]
    log(env["General"]["log"], "Calling [{}]...".format(" ".join(args)))
    return subprocess.call(args, cwd=l1product_path)


def get_band_names_from_nc(nc):
    """Returns a list containing all band names of a given product."""
    if type(nc) is str:
        with Dataset(nc) as nc:
            return get_band_names_from_nc(nc)

    bands = []
    for var in nc.variables:
        if len(nc.variables[var].shape) == 2:
            if hasattr(nc.variables[var], 'orig_name'):
                bands.append(nc.variables[var].orig_name)
            else:
                bands.append(var)
    return bands


def get_name_width_height_from_nc(nc, product_file=None):
    """Returns the height and the width of a given product."""
    for var in nc.variables:
        if len(nc.variables[var].shape) == 2:
            return os.path.splitext(os.path.basename(product_file))[0] if product_file is not None else None,\
                   nc.variables[var].shape[1], nc.variables[var].shape[0]
    raise RuntimeWarning('Could not read width and height from product {}.'.format(product_file))


def get_pixel_pos(longitudes, latitudes, lon, lat, x=None, y=None, step=None):
    """
    Returns the coordinates of the pixel [x, y] which cover a certain geo location (lon/lat).
    :param longitudes: matrix representing the longitude of each pixel
    :param latitudes: matrix representing the latitude of every pixel
    :param lon: longitude of the geo location of interest
    :param lat: latitude of the geo location of interest
    :param x: starting point of the algorithm
    :param y: starting point of the algorithm
    :param step: starting step size of the algorithm
    :return: [-1, -1] if the geo location is not covered by this product
    """

    lons_height, lons_width = len(longitudes), len(longitudes[0])
    lats_height, lats_width = len(latitudes), len(latitudes[0])

    if lats_height != lons_height or lats_width != lons_width:
        raise RuntimeError("Provided latitudes and longitudes matrices do not have the same size!")

    if x is None:
        x = int(lons_width / 2)
    if y is None:
        y = int(lats_height / 2)
    if step is None:
        step = int(ceil(min(lons_width, lons_height) / 4))

    if x + step > lons_height:
        x = int(lons_height - step - 1)
    if y + step > lons_width:
        y = int(lons_width - step - 1)

    if x - step < 0:
        x = step
    if y - step < 0:
        y = step

    new_coords = [[x, y], [x - step, y - step], [x - step, y], [x - step, y + step], [x, y + step],
                  [x + step, y + step], [x + step, y], [x + step, y - step], [x, y - step]]
    distances = [haversine((lat, lon), (latitudes[new_x][new_y], longitudes[new_x][new_y])) for [new_x, new_y] in
                 new_coords]

    idx = distances.index(min(distances))

    if step == 1:
        if x <= 0 or x >= lats_width - 1 or y <= 0 or y >= lats_height - 1:
            return [-1, -1]
        return new_coords[idx]
    else:
        return get_pixel_pos(longitudes, latitudes, lon, lat, new_coords[idx][0], new_coords[idx][1],
                             int(ceil(step / 2)))


def get_valid_pe_from_nc(nc, band_name=None):
    if band_name is None:
        for band_name in nc.variables:
            if 'valid_pixel_expression' in nc.variables[band_name].__dict__.keys():
                return nc.variables[band_name].valid_pixel_expression
    elif 'valid_pixel_expression' in nc.variables[band_name].__dict__.keys():
        return nc.variables[band_name].valid_pixel_expression
    raise RuntimeError('Unable to read valid pixel expression from provided product. Please implement!')


def get_lat_lon_from_x_y_from_nc(nc, x, y, lat_var_name=None, lon_var_name=None):
    if lat_var_name is None:
        if 'latitude' in nc.variables.keys():
            lat_var_name = 'latitude'
        elif 'lat' in nc.variables.keys():
            lat_var_name = 'lat'
        else:
            raise RuntimeError('Cannot guess the name of the latitude variable for this product, please implement.')

    if lon_var_name is None:
        if 'longitude' in nc.variables.keys():
            lon_var_name = 'longitude'
        elif 'lon' in nc.variables.keys():
            lon_var_name = 'lon'
        else:
            raise RuntimeError('Cannot guess the name of the longitude variable for this product, please implement.')

    return get_lat_lon_from_x_y(nc.variables[lat_var_name], nc.variables[lon_var_name], x, y)


def get_lat_lon_from_x_y(lat_var, lon_var, x, y):
    if len(lat_var.dimensions) == 1:
        return lat_var[y], lon_var[x]
    elif len(lat_var.dimensions) == 2:
        return lat_var[y][x], lon_var[y][x]
    else:
        raise RuntimeError('Lat an Lon could not be extracted from the provided lat and lon variables because the'
                           ' dimensions do not match')


def get_pixel_value_xy(nc, band_name, x, y):
    return nc.variables[band_name][y][x]


def get_band_from_nc(nc, band_name):
    return nc.variables[band_name]


def get_pixels_from_nc(nc_path, band_name):
    with Dataset(nc_path) as src:
        band = src.variables[band_name][:]
    return band


def copy_nc(src, dst, included_bands):
    dst.setncatts(src.__dict__)
    for name, dimension in src.dimensions.items():
        dst.createDimension(name, (len(dimension) if not dimension.isunlimited() else None))
    included_bands = ['crs', 'lat', 'lon'] + included_bands
    for name, variable in src.variables.items():
        if name in included_bands:
            dst.createVariable(name, variable.datatype, variable.dimensions, compression='zlib', complevel=6)
            dst[name].setncatts(src[name].__dict__)
            dst[name][:] = src[name][:]


def copy_band(src, dst, band_name):
    for name, variable in src.variables.items():
        if name == band_name:
            dst.createVariable(name, variable.datatype, variable.dimensions, compression='zlib', complevel=6)
            dst[name].setncatts(src[name].__dict__)
            dst[name][:] = src[name][:]


def create_band(dst, band_name, band_unit, valid_pixel_expression):
    b = dst.createVariable(band_name, 'f', dimensions=('lat', 'lon'), fill_value=np.NaN, compression='zlib', complevel=6)
    b.units = band_unit
    b.valid_pixel_expression = valid_pixel_expression
    return b


def read_pixels_from_nc(nc, band_name, x, y, w, h, data=None, dtype=np.float64):
    return read_pixels_from_band(nc.variables[band_name], x, y, w, h, data, dtype)


def read_pixels_from_band(band, x, y, w, h, data=None, dtype=np.float64):
    if data is None:
        data = np.zeros(w * h, dtype=dtype)
    arr = np.array(band[y:y + h, x:x + w], dtype=dtype).flatten()
    data[~np.isnan(arr)] = arr[~np.isnan(arr)]
    return data


def write_pixels_to_nc(nc, band_name, x, y, w, h, data):
    write_pixels_to_band(nc[band_name], x, y, w, h, data)


def write_pixels_to_band(band, x, y, w, h, data):
    band[range(y, y + h), range(x, x + w)] = data.reshape(h, w)


def write_all_pixels_to_nc(nc, band_name, data):
    nc[band_name][:] = data


def append_to_valid_pixel_expression(nc, vpe):
    for band_name in nc.variables.keys():
        band_properties = nc.variables[band_name].__dict__
        if "valid_pixel_expression" in band_properties:
            if vpe not in band_properties["valid_pixel_expression"] and band_properties["valid_pixel_expression"] != "":
                nc.variables[band_name].valid_pixel_expression = band_properties["valid_pixel_expression"] + " and {}".format(vpe)


def get_np_data_type(nc, band_name):
    dtype = nc.variables[band_name].datatype
    if dtype in [np.int8, np.int16, np.int32, np.int64, np.float32, np.float64]:
        return dtype, dtype.name
    elif dtype <= 12:
        return np.int32, 'int32'
    elif dtype == 21:
        return np.float64, 'float64'
    elif dtype == 30:
        return np.float32, 'float32'
    elif dtype == 31:
        return np.float64, 'float64'
    else:
        raise ValueError("Cannot handle band of data_sh type '{}'".format(str(dtype)))
