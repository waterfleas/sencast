#! /usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time

from requests.auth import HTTPBasicAuth
from snappy import ProductIO
from threading import Semaphore, Thread

from packages.auxil import init_hindcast
from packages.earthdata_api import authenticate

# download apis, processors, and adapters are imported dynamically to make hindcast also work on systems,
# where some of them might not be available


def hindcast(params_file, env_file=None, max_parallel_downloads=1, max_parallel_processing=1):
    # read env and params file and copy the params file to l2_path for reproducibility
    env, params, l1_path, l2_path = init_hindcast(env_file, params_file)

    do_hindcast(env, params, l1_path, l2_path, max_parallel_downloads, max_parallel_processing)


def do_hindcast(env, params, l1_path, l2_path, max_parallel_downloads=1, max_parallel_processing=1):
    # decide which API to use
    if env['DIAS']['API'] == "COAH":
        from diasapis.coah_api import get_download_requests, do_download
        auth = HTTPBasicAuth(env['COAH']['username'], env['COAH']['password'])
    elif env['DIAS']['API'] == "HDA":
        from diasapis.hda_api import get_download_requests, do_download
        auth = HTTPBasicAuth(env['HDA']['username'], env['HDA']['password'])
    else:
        raise RuntimeError("Unknown API: {} (possible options are 'HDA' or 'COAH').".format(env['General']['API']))

    # find products which match the criterias from params
    start, end = params['General']['start'], params['General']['end']
    sensor, resolution, wkt = params['General']['sensor'], params['General']['resolution'], params['General']['wkt']
    download_requests, product_names = get_download_requests(auth, start, end, sensor, resolution, wkt)
    print("Found {} product(s) which are handled by indivitual threads".format(len(product_names)))

    # set up inputs for product hindcast
    l1_product_paths = [os.path.join(l1_path, product_name) for product_name in product_names]
    semaphores = {'download': Semaphore(max_parallel_downloads), 'processing': Semaphore(max_parallel_processing)}

    # print information about available products
    actual_downloads = len([0 for l1_product_path in l1_product_paths if not os.path.exists(l1_product_path)])
    print("{} products are already available.".format(len(l1_product_paths) - actual_downloads))
    print("{} products must be downloaded first.".format(actual_downloads))

    # authenticate for earth data api
    authenticate(env['Earthdata']['username'], env['Earthdata']['password'])

    # do hindcast for every product
    hindcast_threads = []
    for download_request, l1_product_path in zip(download_requests, l1_product_paths):
        args = (env, params, do_download, auth, download_request, l1_product_path, l2_path, semaphores)
        hindcast_threads.append(Thread(target=hindcast_product, args=args))
        hindcast_threads[-1].start()

    # wait for all hindcast threads to terminate
    starttime = time.time()
    for hindcast_thread in hindcast_threads:
        hindcast_thread.join()
    print("Hindcast complete in {0:.1f} seconds.".format(time.time() - starttime))


def hindcast_product(env, params, download_method, auth, download_request, l1_product_path, l2_path, semaphores):
    # download products, which are not yet available
    if not os.path.exists(l1_product_path):
        with semaphores['download']:
            download_method(auth, download_request, l1_product_path)

    with semaphores['processing']:
        # FOR S3 MAKE SURE THE NON-DEFAULT S3TBX SETTING IS SELECTED IN THE SNAP PREFERENCES!
        if "OLCI" == params['General']['sensor']:
            product = ProductIO.readProduct(l1_product_path)
            if 'PixelGeoCoding2' not in str(product.getSceneGeoCoding()):
                raise RuntimeError("Pixelwise geocoding is not activated for S3TBX, please check the settings in SNAP!")
            product.closeIO()

        # process the products
        if "IDEPIX" in params['General']['preprocessor'].split(","):
            from processors.idepix import idepix
            l1p = idepix.process(env, params, l1_product_path, l1_product_path, l2_path)
        if "C2RCC" in params['General']['processors'].split(","):
            from processors.c2rcc import c2rcc
            l2c2rcc = c2rcc.process(env, params, l1_product_path, l1p, l2_path)
        if "POLYMER" in params['General']['processors'].split(","):
            from processors.polymer import polymer
            l2poly = polymer.process(env, params, l1_product_path, l1p, l2_path)
        if "MPH" in params['General']['processors'].split(","):
            from processors.mph import mph
            l2mph = mph.process(env, params, l1_product_path, l1p, l2_path)

    # apply adapters
    if "DATALAKES" in params['General']['adapters'].split(","):
        from adapters import datalakes
        if "C2RCC" == params['DATALAKES']['input_processor']:
            datalakes.apply(env, params, l2c2rcc)
        if "POLYMER" == params['DATALAKES']['input_processor']:
            datalakes.apply(env, params, l2poly)
        if "MPH" == params['DATALAKES']['input_processor']:
            datalakes.apply(env, params, l2mph)
