#! /usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import subprocess
import re
import os

import numpy as np

from snappy import jpy, GPF, ProductIO, ProductUtils
from datetime import datetime
from packages.product_fun import get_corner_pixels_ROI, get_UL_LR_geo_ROI
from packages.ancillary import Ancillary_NASA
from packages.auxil import gpt_xml

from packages import path_config
sys.path.append(path_config.polymer_path)

from polymer.main import run_atm_corr
from polymer.main import Level1, Level2
from polymer.level1_msi import Level1_MSI
from polymer.gsw import GSW
from polymer.level2 import default_datasets


class MyProduct(object):
    """This class is used to process a snappy Product. It contains a list of snappy Product which should all be of the same 
    kind (OLCI or MSI)."""
    
    HashMap = jpy.get_type('java.util.HashMap')
    GPF.getDefaultInstance().getOperatorSpiRegistry().loadOperatorSpis()
    BandDescriptor = jpy.get_type('org.esa.snap.core.gpf.common.BandMathsOp$BandDescriptor')
        
    def __init__(self, productslist, params, productspath):
        self.date = []
        self.date_str = []
        self.products = productslist
        self.path = productspath
        self.product_dict = {} # If you have a date, gives you the idx for the class lists
        self.params = params
        self.band_names = []
        self.band_names_str = ''
        self.state = []
        self.valid_pixels = [] # percentage of valid pixels
        self.masks = []
        self.update()
        
        
    def __repr__(self):
        dispstr = 'Product List:'
        c = 0
        for product in self.products:
            dispstr += '\n'+product.getName()
            if self.valid_pixels:
                dispstr += '\n{} % of valid pixels'.format(self.valid_pixels[c])
            c += 1
        return dispstr
    
    
    def update_date(self):
        """ Update field date with a list of datetime for each product"""
        dtlist = []
        dtstrlist = []
        for product in self.products:
            name = product.getName()
            name = re.search('\d{8}T\d{6}', name).group(0)
            dt = datetime.strptime(name, '%Y%m%dT%H%M%S')
            dtlist.append(dt)
            dtstrlist.append(dt.strftime('%Y%m%dT%H%M'))
        self.date = dtlist
        self.date_str = dtstrlist
        
      
    def update_dict(self):
        """ Update product dictionary.
        - the key is the date of the product (YYYYMMDD)
        - the value is the corresponding product"""
        c = 0
        dicttemp = {}
        # If self.date and self.products have different size update the dates
        if len(self.products) != len(self.date):
            self.update_date()
        c = 0
        for product in self.products:
            dicttemp[self.date_str[c]] = c
            c += 1
        self.product_dict = dicttemp
        
        
    def update_band_names(self):
        """ Update product band names """
        bnames = []
        for bn in self.products[0].getBandNames():
            bnames.append(bn)
        bnstr = bnames[0]
        for bn in bnames[1:]:
            bnstr += ','+bn
        self.band_names = bnames
        self.band_names_str = bnstr

        
    def update(self):
        self.update_date()
        self.update_dict()
        self.update_band_names()
    
    
    def get_band_names(self, pattern=''):
        return [bn for bn in self.products[0].getBandNames() if pattern in bn]
    
    
    def resample(self, res=20):
        parameters = MyProduct.HashMap()
        parameters.put('targetResolution', res)
        results = []
        for product in self.products:
            result = GPF.createProduct('Resample', parameters, product)
            pname = result.getName()
            result.setName(pname+'_'+str(res)+'m')
            results.append(result)
        self.products = results
        self.state.append('Resample')
        self.update()

    
    def subset(self, regions=None):
        # Update MyProduct
        self.update()
        subsets = []
        c = 0
        for product in self.products:
            h = product.getSceneRasterHeight()
            w = product.getSceneRasterWidth()
            print('    Subsetting {}...'.format(product.getName()))
            print('    Size before subset: {}, {}'.format(h, w))
            # Initialisation
            parameters = MyProduct.HashMap()
            if regions is None:
                parameters.put('geoRegion', self.params['wkt'])
            else:
                parameters.put('region', regions[c])
            
            parameters.put('sourceBands', self.band_names_str)
            parameters.put('copyMetadata', 'True')
            try:
                result = GPF.createProduct('Subset', parameters, product)
            except RuntimeError:
                print('    Failed. ROI probably outside the frame, or bad wkt.')
                result = product
            else:
                # Append subset if not empty
                if 'Subset' in result.getName():
                    h = result.getSceneRasterHeight()
                    w = result.getSceneRasterWidth()
                    print('    Size after subset: {}, {}'.format(h, w))
                    subsets.append(result)
                    print('    Subsetting completed\n')
                else:
                     print('    ROI outside the frame')
            c += 1
        if not subsets:
            print('\n    No products passed the subsetting... exiting\n')
            self.products = []
            return
            
        self.products = subsets
        self.state.append('Subset')
        self.update()
        
        
    def copyMasks(self, sourcemyproduct):
        count = 0
        products = []
        for product in self.products:
            sourceproduct = sourcemyproduct.products[count]
            ProductUtils.copyMasks(sourceproduct, product)
            ProductUtils.copyOverlayMasks(sourceproduct, product)
            ProductUtils.copyFlagCodings(sourceproduct, product)
            ProductUtils.copyFlagBands(sourceproduct, product, True)
            products.append(product)
            count += 1
        self.products = products
        self.state.append('Mask')
        self.update()
        
        
    def get_flags(self, pattern=[], match=[]):
        """ This function returns a flag list with one field per product. each element of the list is a dictionnary 
        with the key corresponding to the flag name and the value is a numpy array (uint32) with the corresponding
        flag"""
        flags = []
        for product in self.products:
            h = product.getSceneRasterHeight()
            w = product.getSceneRasterWidth()
            flagNames = product.getAllFlagNames()
            if pattern and not match:
                flagNames = [fn for fn in flagNames if pattern in fn]
            elif match:
                flagNames = [fn for fn in flagNames if fn == match]
            flag = {}
            flag['no flag'] = np.zeros((h, w), dtype=np.uint32)
            for i in range(len(flagNames)):
                params = MyProduct.HashMap()
                targetBand = MyProduct.BandDescriptor()
                targetBand.name = flagNames[i]
                targetBand.type = 'uint32'
                targetBand.expression = flagNames[i]
                targetBands = jpy.array('org.esa.snap.core.gpf.common.BandMathsOp$BandDescriptor', 1)
                targetBands[0] = targetBand
                params.put('targetBands', targetBands)
                
                flagProduct = GPF.createProduct('BandMaths', params, product)
                band = flagProduct.getBand(targetBand.name)
                bandnp = np.zeros(h*w, dtype=np.uint32)
                band.readPixels(0, 0, w, h, bandnp)
                bandnp.shape = h, w
                flag[targetBand.name] = bandnp
                flagProduct.closeIO()
            flags.append(flag)
        return flags

        
    def get_regions(self):
        regions = []
        for product in self.products:
            UL, UR, LR, LL = get_corner_pixels_ROI(product, self.params)
            w = LR[1] - UL[1]
            h = LR[0] - UL[0]
            regions.append(str(UL[1])+','+str(UL[0])+','+str(w)+','+str(h))
        return regions
    
    
    def idepix(self, pmode):
        results_idpx = []
        parameters = MyProduct.HashMap()
        if self.params['sensorname'] == 'MSI':
            parameters.put('computeMountainShadow', True)
            parameters.put('computeCloudShadow', True)
            parameters.put('computeCloudBuffer', True)
            parameters.put('computeCloudBufferForCloudAmbiguous', True)
            parameters.put('cloudBufferWidth', 2)

        for product in self.products:
            if pmode in ['1', '2']:
                resultIdpx = GPF.createProduct('Idepix.Sentinel'+\
                                               str(self.params['satnumber'])+\
                                               self.params['sensorname'],
                                               parameters, product)
            if pmode == '3':
                if self.params['sensor'].lower() == 'olci':
                    op_str = 'Idepix.Sentinel3.Olci'
                elif self.params['sensor'].lower() == 'msi':
                    op_str = 'Idepix.Sentinel2'


                print()




            ProductUtils.copyOverlayMasks(resultIdpx, product)
            ProductUtils.copyFlagBands(resultIdpx, product, True)
            pname = product.getName()
            product.setName('L1P_'+pname)
            results_idpx.append(product)
        
        self.products = results_idpx
        self.state.append('Idepix')
        self.update()
        
        
    def c2rcc(self, pmode, read_dir='', write_dir=''):
        results = []
        c = 0
        for product in self.products:
            parameters = MyProduct.HashMap()
            parameters.put('validPixelExpression', self.params['validexpression'])
            print(self.params['validexpression'])
            if self.params['sensor'].upper() == 'MSI':
                # GET ANCILLARY
                # cd to jupyter/sentinel_hindcast directory
                cwd = os.getcwd()
                os.chdir(path_config.polymer_path)
                ancillary = Ancillary_NASA()
                os.chdir(cwd)
                lat, lon = get_UL_LR_geo_ROI(product, self.params)
                lat = np.nanmean(lat)
                lon = np.nanmean(lon)
                coords = lat, lon
                try:
                    ozone = ancillary.get('ozone', self.date[c])
                    ozone = np.round(ozone[coords])
                except:
                    ozone = 300.
                    pass
                try:
                    surfpress = ancillary.get('surf_press', self.date[c])
                    surfpress = np.round(surfpress[coords])
                except:
                    surfpress = 1000.
                parameters.put('ozone', ozone)
                parameters.put('press', surfpress)
                parameters.put('salinity', 0.05)
                print('Default salinity is 0.05 PSU for freshwater')
            else:
                parameters.put('useEcmwfAuxData', True)
            c += 1
            if self.params['c2rcc altnn'] != '':
                print('Using alternative NN specified in param file...')
                parameters.put('alternativeNNPath', self.params['c2rcc altnn'])
            else:
                print('Using default NN...')
            if pmode in ['1', '2']:
                resultc2r = GPF.createProduct('c2rcc.'+self.params['sensor'].lower(), parameters, product)
                pname = product.getName().split('.')[0]
                if self.params['sensor'].upper() == 'OLCI':
                    print('Reprojecting C2RCC output...')
                    # Reprojection (UTM)
                    parameters = MyProduct.HashMap()
                    parameters.put('crs', 'EPSG:32662')
                    reprProduct = GPF.createProduct("Reproject", parameters, resultc2r)
                    newname = 'L2C2R_reproj_'+pname
                    reprProduct.setName(newname)
                    results.append(reprProduct)
                else:
                    newname = 'L2C2R_'+pname
                    resultc2r.setName(newname)
                    results.append(resultc2r)
            if pmode == '3':
                op_str = 'c2rcc.' + self.params['sensor'].lower()
                xml_path = './temp.xml'
                pname = product.getName() + '.nc'
                product_path = read_dir + '/' + pname
                if self.params['sensor'].upper() == 'OLCI':
                    newname = 'L2C2R_reproj_' + pname
                else:
                    newname = 'L2C2R_'+pname
                target_path = read_dir + '/../' + 'L2C2R/' + newname
                gpt_xml(operator=op_str, product_parameters=parameters, xml_path=xml_path)
                subprocess.call([path_config.gpt_path, xml_path, '-SsourceProduct=' + product_path, '-PtargetProduct=' + target_path])
                os.remove('./temp.xml')
                reprProduct = ProductIO.readProduct(target_path)
                reprProduct.setName(newname)
                results.append(reprProduct)

        self.products = results
        self.state.append('c2rcc')
        self.update()


    def mph(self):
        if self.params['sensor'].upper() == 'OLCI':
            parameters = MyProduct.HashMap()
            parameters.put('validPixelExpression', self.params['validexpression'])
            parameters.put('exportMph', True)
            parametersrepr = MyProduct.HashMap()
            parametersrepr.put('crs', 'EPSG:32662')
            results = []
            for product in self.products:
                print('apply MPH...')
                resultmph = GPF.createProduct('MphChl', parameters, product)
                print('Done.')
                pname = product.getName().split('.')[0]
                # Reproject
                print('Reprojecting MPH output...')
                reprProduct = GPF.createProduct("Reproject", parametersrepr, resultmph)
                newname = 'L2MPH_reproj_'+pname
                reprProduct.setName(newname)
                results.append(reprProduct)
                print('Done.')
            self.products = results
            self.state.append('MPH')
            self.update()
    
    
    def polymer(self, masterProduct=None, params=None):
        """ Apply POLYMER atmospheric correction."""
        # Polymer needs original input files and will fail with L1P generated with SNAP

        # ToDo: merge L2 Polymer output with the L1P product

        results = []
        # To use POLYMER, we need to work from its home directory
        cwd = os.getcwd()
        os.chdir(path_config.polymer_path)
        # Get intersection between wkt and products coordinates
        ULs = []
        LRs = []
        # If MSI we need to find the coordinates on a resampled product
        # Note that the new product needs the original file names
        nametemp = [p.getName() for p in self.products]

        if self.params['sensor'].upper() == 'MSI':
            res = int(params['resolution'])
            tempmyproduct = MyProduct(self.products, self.params, self.path)
            tempmyproduct.resample(res=res)
            products = []
            for p in tempmyproduct.products:
                p.setName(p.getName()[:-14])
                products.append(p)
        else:
            products = self.products
        
        for product in products:
            temppath = [os.path.join(self.path, p) for p in os.listdir(self.path) if \
                        product.getName().split('.')[0] in p]
            temppath = [os.path.join(temppath[0], p) for p in os.listdir(temppath[0]) if \
                        product.getName().split('.')[0] in p]
            ppath = temppath[0]
            UL, UR, LR, LL = get_corner_pixels_ROI(product, self.params)

            w = LR[1] - UL[1]
            h = LR[0] - UL[0]

            # make sure S-2 subsets comprise of an integer number of 60 m pixels
            if self.params['sensor'].upper() == 'MSI':
                while not  (w / 6).is_integer():
                    LR[1] += 1
                    w = LR[1] - UL[1]
                while not (h / 6).is_integer():
                    LR[0] += 1
                    h = LR[0] - UL[0]
                sline = UL[0]
                scol =  UL[1]
                eline = UL[0] + h
                ecol =  UL[1] + w
            # or subset a somewhat larger are for distorted S-3 images
            else:
                sline = min(UL[0], UR[0])
                eline = max(LL[0], LR[0])
                scol = min(UL[1], UR[1])
                ecol = max(LL[1], LR[1])

            savdir = path_config.polymer_path
            if not os.path.isdir(savdir):
                os.mkdir(savdir)
            pfname = os.path.join(savdir, 'temp.nc')

            # If file already exist remove it 
            if os.path.isfile(pfname):
                os.remove(pfname)

            # Run AC and write product to disk
            print('Applying Polymer...')
            if self.params['sensor'].upper() == 'MSI':
                temppath = [os.path.join(ppath, tp) for tp in os.listdir(ppath) if 'GRANULE' in tp]
                temppath = [os.path.join(temppath[0], tp) for tp in os.listdir(temppath[0]) \
                            if 'L1C' in tp]
                assert len(temppath) == 1
                ppath = temppath[0]
                run_atm_corr(Level1_MSI(ppath, sline=sline, scol=scol, eline=eline,ecol=ecol, landmask=GSW(),
                                        resolution=res), Level2(filename=pfname, fmt='netcdf4', overwrite=True, datasets=default_datasets+['sza']))
            else:
                if not os.path.isdir('data_landmask_gsw'):
                    os.mkdir('data_landmask_gsw')
                run_atm_corr(Level1(ppath, sline=sline, scol=scol, eline=eline, ecol=ecol, landmask=GSW(agg=8)),
                    Level2(filename=pfname, fmt='netcdf4', overwrite=True, datasets=default_datasets+['sza']))

            resultpoly = ProductIO.readProduct(pfname)
            print('Polymer applied')

            pname = product.getName().split('.')[0]
            if masterProduct is not None:
                newname = 'L2POLY_reproj_L1P_' + pname
                coll_parameters = MyProduct.HashMap()
                sourceProducts = MyProduct.HashMap()
                sourceProducts.put('master', masterProduct)
                sourceProducts.put('slave', resultpoly)
                coll_parameters.put('renameMasterComponents', False)
                coll_parameters.put('renameSlaveComponents', False)
                mergedProduct = GPF.createProduct('Collocate', coll_parameters, sourceProducts)

                subs_parameters = MyProduct.HashMap()
                subs_parameters.put('bandNames', 'OAA,OZA,SAA,SZA,Rw400,Rw412,Rw443,Rw490,Rw510,' +
                                                 'Rw560,Rw620,Rw665,Rw681,Rw709,Rw754,Rw779,Rw865,Rw1020,' +
                                                 'Rnir,Rgli,logchl,bbs,bitmask,quality_flags,pixel_classif_flags')
                subs_parameters.put('copyMetadata', True)
                bandsetProduct = GPF.createProduct('Subset', subs_parameters, mergedProduct)
            else:
                newname = 'L2POLY_' + pname

            bandsetProduct.setName(newname)
            results.append(bandsetProduct)

        self.products = results
        self.state.append('polymer')

        self.update()

        os.chdir(cwd)
    
    
    def write(self, writedir):
        writer = ProductIO.getProductWriter('NetCDF-BEAM') # -CF writer does not add wavelength attributes
        for product in self.products:
            # If file already exist remove it 
            fname = os.path.join(writedir, product.getName()+'.nc')
            if os.path.isfile(fname):
                os.remove(fname)
            product.setProductWriter(writer)
            product.writeHeader(fname)
            ProductIO.writeProduct(product, fname, 'NetCDF-BEAM') # -CF writer does not add wavelength attributes


    def close(self):
        for product in self.products:
            product.closeIO()
   
