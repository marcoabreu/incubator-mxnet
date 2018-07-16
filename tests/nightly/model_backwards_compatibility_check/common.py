#!/usr/bin/env python

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.


import boto3
import mxnet as mx
import json
import os
import numpy as np
import logging
from mxnet import nd, autograd, gluon
import mxnet.ndarray as nd
from mxnet.gluon.data.vision import transforms, datasets
from mxnet import autograd as ag
import mxnet.ndarray as F
from mxnet.gluon import nn, rnn
import re
import time
import sys
from mxnet.test_utils import assert_almost_equal

# Set fixed random seeds.
mx.random.seed(7)
np.random.seed(7)
logging.getLogger().setLevel(logging.DEBUG)

# get the current mxnet version we are running on
mxnet_version = mx.__version__
model_bucket_name = 'mxnet-model-backwards-compatibility-models'
data_folder = 'mxnet-model-backwards-compatibility-data'
backslash = '/'
s3 = boto3.resource('s3')
ctx = mx.cpu(0)

def get_module_api_model_definition():
    input = mx.symbol.Variable('data')
    input = mx.symbol.Flatten(data=input)

    fc1 = mx.symbol.FullyConnected(data=input, name='fc1', num_hidden=128)
    act1 = mx.sym.Activation(data=fc1, name='relu1', act_type="relu")
    fc2 = mx.symbol.FullyConnected(data=fc1, name='fc2', num_hidden=2)
    op = mx.symbol.SoftmaxOutput(data=fc2, name='softmax')
    model = mx.mod.Module(symbol=op, context=ctx, data_names=['data'], label_names=['softmax_label'])
    return model

def save_inference_results(inference_results, model_name):
    assert (isinstance(inference_results, mx.ndarray.ndarray.NDArray))
    mx.nd.save(model_name + '-inference', {'inference' : inference_results})

def load_inference_results(model_name):
    inf_dict = mx.nd.load(model_name+'-inference')
    return inf_dict['inference']

def save_data_and_labels(test_data, test_labels, model_name):
    assert (isinstance(test_data, mx.ndarray.ndarray.NDArray))
    assert (isinstance(test_labels, mx.ndarray.ndarray.NDArray))
    mx.nd.save(model_name + '-data', {'data' : test_data, 'labels' : test_labels})

def upload_data_and_labels_to_s3(model_name):
    s3 = boto3.client('s3')
    file = model_name + '-data'
    s3.upload_file(file, model_bucket_name, data_folder + backslash +  file)
    print ('data files successfully uploaded to s3')

def upload_model_files_to_s3(files, folder_name):
    s3 = boto3.client('s3')
    for file in files:
        s3.upload_file(file, model_bucket_name, folder_name + file)

def clean_model_files(files, model_name):
    files.append(model_name + '-inference')
    files.append(model_name + '-data')

    for file in files:
        if os.path.isfile(file):
            os.remove(file)

def download_data_from_s3(model_name):
    print ('Downloading data files for %s from bucket %s'%(model_name, model_bucket_name + backslash + data_folder))
    bucket = s3.Bucket(model_bucket_name)
    prefix = data_folder + backslash + model_name + '-data'
    data_files_meta = list(bucket.objects.filter(Prefix = prefix))
    if len(data_files_meta) == 0:
        print ('No data files found for %s' %model_name)
        return None

    bucket.download_file(data_folder + backslash + model_name+'-data', model_name+'-data')

    data = mx.nd.load(model_name+'-data')
    return data

def download_model_files_from_s3(model_name):
    for folder in get_top_level_folders_in_bucket(s3, model_bucket_name):
        bucket = s3.Bucket(model_bucket_name)
        prefix = folder + backslash + model_name
        model_files_meta = list(bucket.objects.filter(Prefix = prefix))
        if len(model_files_meta) == 0:
            print ('No trained models found under path : %s' %prefix)
            continue
        model_files = list()
        for obj in model_files_meta:
            file_name = obj.key.split('/')[2]
            model_files.append(file_name)
            ## Download this file---
            bucket.download_file(obj.key, file_name)

    return model_files

def get_top_level_folders_in_bucket(s3client, bucket_name):
    '''This function returns the top level folders in the S3Bucket. These folders help us to navigate to the trained model files stored for different MXNet versions. '''
    bucket = s3client.Bucket(bucket_name)
    result = bucket.meta.client.list_objects(Bucket=bucket.name,
                                         Delimiter=backslash)
    folder_list = list()
    if 'CommonPrefixes' not in result:
        print ('No trained models found in S3 bucket : %s for this file. Please train the models and run inference again' %bucket_name)
        return
    for obj in result['CommonPrefixes']:
        folder_name = obj['Prefix'].strip(backslash)
        # The top level folders contain MXNet Version # for trained models. Skipping the data folder here
        if folder_name == data_folder:
            continue
        folder_list.append(obj['Prefix'].strip(backslash))

    return folder_list

class Net(gluon.Block):
    def __init__(self, **kwargs):
        super(Net, self).__init__(**kwargs)
        with self.name_scope():
            # layers created in name_scope will inherit name space
            # from parent layer.
            self.conv1 = nn.Conv2D(20, kernel_size=(5,5))
            self.pool1 = nn.MaxPool2D(pool_size=(2,2), strides = (2,2))
            self.conv2 = nn.Conv2D(50, kernel_size=(5,5))
            self.pool2 = nn.MaxPool2D(pool_size=(2,2), strides = (2,2))
            self.fc1 = nn.Dense(500)
            self.fc2 = nn.Dense(2)

    def forward(self, x):
        x = self.pool1(F.tanh(self.conv1(x)))
        x = self.pool2(F.tanh(self.conv2(x)))
        # 0 means copy over size from corresponding dimension.
        # -1 means infer size from the rest of dimensions.
        x = x.reshape((0, -1))
        x = F.tanh(self.fc1(x))
        x = F.tanh(self.fc2(x))
        return x

class HybridNet(gluon.HybridBlock):
    def __init__(self, **kwargs):
        super(HybridNet, self).__init__(**kwargs)
        with self.name_scope():
            # layers created in name_scope will inherit name space
            # from parent layer.
            self.conv1 = nn.Conv2D(20, kernel_size=(5,5))
            self.pool1 = nn.MaxPool2D(pool_size=(2,2), strides = (2,2))
            self.conv2 = nn.Conv2D(50, kernel_size=(5,5))
            self.pool2 = nn.MaxPool2D(pool_size=(2,2), strides = (2,2))
            self.fc1 = nn.Dense(500)
            self.fc2 = nn.Dense(2)

    def hybrid_forward(self, F, x):
        x = self.pool1(F.tanh(self.conv1(x)))
        x = self.pool2(F.tanh(self.conv2(x)))
        # 0 means copy over size from corresponding dimension.
        # -1 means infer size from the rest of dimensions.
        x = x.reshape((0, -1))
        x = F.tanh(self.fc1(x))
        x = F.tanh(self.fc2(x))
        return x

class SimpleLSTMModel(gluon.Block):
    def __init__(self, **kwargs):
        super(SimpleLSTMModel, self).__init__(**kwargs)
        with self.name_scope():
            self.model = mx.gluon.nn.Sequential(prefix='')
            with self.model.name_scope():
                self.model.add(mx.gluon.nn.Embedding(30, 10))
                self.model.add(mx.gluon.rnn.LSTM(20))
                self.model.add(mx.gluon.nn.Dense(100))
                self.model.add(mx.gluon.nn.Dropout(0.5))
                self.model.add(mx.gluon.nn.Dense(2, flatten=True, activation='tanh'))


    def forward(self, x):
        return self.model(x)

def compare_versions(version1, version2):
    '''
    https://stackoverflow.com/questions/1714027/version-number-comparison-in-python
    '''
    def normalize(v):
        return [int(x) for x in re.sub(r'(\.0+)*$','', v).split(".")]
    return cmp(normalize(version1), normalize(version2))