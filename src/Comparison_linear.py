'''
Created on Sep 15, 2016

@author: urishaham
'''


import os.path
import keras.optimizers
from Calibration_Util import DataHandler as dh 
from Calibration_Util import FileIO as io
from Calibration_Util import Misc
from keras.layers import Input, Dense, merge, Dropout
from keras.models import Model
from keras import callbacks as cb
import numpy as np
import matplotlib
matplotlib.use('TkAgg')
from matplotlib import pyplot as plt
import CostFunctions as cf
import Monitoring as mn
from keras.regularizers import l2
from sklearn import decomposition
from keras.callbacks import LearningRateScheduler
import math
import ScatterHist as sh
from numpy import genfromtxt
import sklearn.preprocessing as prep
from keras import initializations
from keras.layers import Input, Dense, merge, Activation
from keras.layers.normalization import BatchNormalization


# configuration hyper parameters
denoise = True # wether or not to train a denoising autoencoder to remover the zeros
keepProb=.8

# AE confiduration
ae_encodingDim = 25
l2_penalty_ae = 1e-3 

#MMD net configuration
mmdNetLayerSizes = [25, 25]
l2_penalty = 5e-3
init = lambda shape, name:initializations.normal(shape, scale=.1e-4, name=name)



######################
###### get data ######
######################
# we load two CyTOF samples 
# Labels are cell types, they are not used in the calibration.

sampleAPath = os.path.join(io.DeepLearningRoot(),'Data/Person1Day1.csv')
sampleBPath = os.path.join(io.DeepLearningRoot(),'Data/Person1Day2.csv')

source = genfromtxt(sampleAPath, delimiter=',', skip_header=0)
target = genfromtxt(sampleBPath, delimiter=',', skip_header=0)

numZerosOK=1
toKeepS = np.sum((source==0), axis = 1) <=numZerosOK
print(np.sum(toKeepS))
toKeepT = np.sum((target==0), axis = 1) <=numZerosOK
print(np.sum(toKeepT))

inputDim = target.shape[1]

if denoise:
    trainTarget_ae = np.concatenate([source[toKeepS], target[toKeepT]], axis=0)
    trainData_ae = trainTarget_ae * np.random.binomial(n=1, p=keepProb, size = trainTarget_ae.shape)
    input_cell = Input(shape=(inputDim,))
    encoded = Dense(ae_encodingDim, activation='relu',W_regularizer=l2(l2_penalty_ae))(input_cell)
    decoded = Dense(inputDim, activation='linear',W_regularizer=l2(l2_penalty_ae))(encoded)
    autoencoder = Model(input=input_cell, output=decoded)
    autoencoder.compile(optimizer='adam', loss='mse')
    autoencoder.fit(trainData_ae, trainTarget_ae, nb_epoch=200, batch_size=128, shuffle=True,  validation_split=0.1,
                    callbacks=[mn.monitor(), cb.EarlyStopping(monitor='val_loss', patience=10,  mode='auto')])    
    source = autoencoder.predict(source)
    target = autoencoder.predict(target)

# rescale the data to have zero mean and unit variance
preprocessor = prep.StandardScaler().fit(source)
source = preprocessor.transform(source)  
target = preprocessor.transform(target)    


###############################################################
###### calibration by shifting and rescaling each marker ######
###############################################################

print('linear calibration by matching means and variances')
mt = np.mean(target, axis = 0)
vt = np.var(target, axis = 0)
ms = np.mean(source, axis = 0)
vs = np.var(source, axis = 0)

source_train_Z = np.zeros(source.shape)
dim = target.shape[1]
for i in range(dim):
    source_train_Z[:,i] = (source[:,i] - ms[i]) / np.sqrt(vs[i]) * np.sqrt(vt[i]) + mt[i]
    
# MMD with the a single scale at a time, for various scales
scales = [3e-1, 1e-0, 3e-0, 1e1]
TT, OT_Z, ratios = Misc.checkScales(target, source_train_Z, scales, nIters = 10)
print('scales: ', scales)
print('MMD(target,target): ', TT)
print('MMD(after calibration, target): ', OT_Z)

# MMD(target,target):              [ 0.04468636  0.04473211  0.0377084   0.01835019]
# MMD(after calibration, target):  [ 0.04655762  0.07034409  0.07571652  0.02227357]

### 
pca = decomposition.PCA()
pca.fit(target)

# project data onto PCs
target_sample_pca = pca.transform(target)
projection_before = pca.transform(source)
projection_after = pca.transform(source_train_Z)

pc1 = 0
pc2 = 1
sh.scatterHist(target_sample_pca[:,pc1], target_sample_pca[:,pc2], projection_before[:,pc1], projection_before[:,pc2])
sh.scatterHist(target_sample_pca[:,pc1], target_sample_pca[:,pc2], projection_after[:,pc1], projection_after[:,pc2])

##########################################
###### removing principal component ######
##########################################

n_target = target.shape[0]
n_source = source.shape[0]
batch = np.concatenate([np.ones(n_target), 2*np.ones(n_source)], axis=0)
combinedData = np.concatenate([target, source], axis=0)
pca = decomposition.PCA()
pca.fit(combinedData)
combinedData_proj = pca.transform(combinedData)
# measure correlation between each PC and the batch
dim = target.shape[1]
corrs = np.zeros(dim)
for i in range(dim):
    corrs[i] = np.corrcoef(batch, combinedData_proj[:,i])[0,1]
print('correlation coefficients: ')
print(corrs)
toTake = np.argsort(np.abs(corrs))[range(dim-1)] 


combinedDataRecon = np.dot(combinedData_proj[:,toTake], pca.components_[[toTake]]) + np.mean(combinedData, axis=0)

target_train_pca = combinedDataRecon[:n_target,]
source_train_pca = combinedDataRecon[n_target:,]
# MMD with the a single scale at a time, for various scales
scales = [3e-1, 1e-0, 3e-0, 1e1]
TT, OT_pca, ratios = Misc.checkScales(target_train_pca, source_train_pca, scales, nIters = 10)
print('scales: ', scales)
print('MMD(target,target): ', TT)
print('MMD(after calibration,target): ', OT_pca)
# MMD(target,target):             [ 0.04453892  0.04346298  0.04182024  0.01648208]
# MMD(after calibration,target):  [ 0.04740933  0.11866774  0.16063778  0.07313088]

### 
pca = decomposition.PCA()
pca.fit(target)

# project data onto PCs
target_sample_pca = pca.transform(target_train_pca)
projection_after = pca.transform(source_train_pca)

pc1 = 0
pc2 = 1
#sh.scatterHist(target_sample_pca[:,pc1], target_sample_pca[:,pc2], projection_before[:,pc1], projection_before[:,pc2])
sh.scatterHist(target_sample_pca[:,pc1], target_sample_pca[:,pc2], projection_after[:,pc1], projection_after[:,pc2])



####################
###### ResNet ######
####################
calibInput = Input(shape=(inputDim,))
block1_bn1 = BatchNormalization()(calibInput)
block1_a1 = Activation('relu')(block1_bn1)
block1_w1 = Dense(mmdNetLayerSizes[0], activation='linear',W_regularizer=l2(l2_penalty), init = init)(block1_a1) 
block1_bn2 = BatchNormalization()(block1_w1)
block1_a2 = Activation('relu')(block1_bn2)
block1_w2 = Dense(inputDim, activation='linear',W_regularizer=l2(l2_penalty), init = init)(block1_a2) 
block1_output = merge([block1_w2, calibInput], mode = 'sum')
block2_bn1 = BatchNormalization()(block1_output)
block2_a1 = Activation('relu')(block2_bn1)
block2_w1 = Dense(mmdNetLayerSizes[1], activation='linear',W_regularizer=l2(l2_penalty), init = init)(block2_a1) 
block2_bn2 = BatchNormalization()(block2_w1)
block2_a2 = Activation('relu')(block2_bn2)
block2_w2 = Dense(inputDim, activation='linear',W_regularizer=l2(l2_penalty), init = init)(block2_a2) 
block2_output = merge([block2_w2, block1_output], mode = 'sum')

calibMMDNet = Model(input=calibInput, output=block2_output)

# learning rate schedule
def step_decay(epoch):
    initial_lrate = 0.01
    drop = 0.5
    epochs_drop = 25.0
    lrate = initial_lrate * math.pow(drop, math.floor((1+epoch)/epochs_drop))
    return lrate
lrate = LearningRateScheduler(step_decay)

#train MMD net
optimizer = keras.optimizers.rmsprop(lr=0.0)

calibMMDNet.compile(optimizer=optimizer, loss=lambda y_true,y_pred: 
               cf.MMD(block2_output,target,MMDTargetValidation_split=0.1).KerasCost(y_true,y_pred))
sourceLabels = np.zeros(source.shape[0])
calibMMDNet.fit(source,sourceLabels,nb_epoch=500,batch_size=1000,validation_split=0.1,verbose=1,
           callbacks=[lrate,mn.monitorMMD(source, target, calibMMDNet.predict),
                      cb.EarlyStopping(monitor='val_loss',patience=50,mode='auto')])

calibratedSource = calibMMDNet.predict(source)

scales = [3e-1, 1e-0, 3e-0, 1e1]
TT, OT_fullNet, ratios = Misc.checkScales(target, calibratedSource, scales, nIters=10)
print('scales: ', scales)
print('MMD(target,target): ', TT)
print('MMD(after calibration, target): ', OT_fullNet)
#MMD(target,target):              [ 0.04472091  0.04506061  0.04096356  0.01826212]
#MMD(after calibration, target):  [ 0.04665827  0.05394157  0.05176455  0.02817073]

### 
pca = decomposition.PCA()
pca.fit(target)

# project data onto PCs
target_sample_pca = pca.transform(target)
projection_before = pca.transform(source)
projection_after = pca.transform(calibratedSource)

pc1 = 0
pc2 = 1
sh.scatterHist(target_sample_pca[:,pc1], target_sample_pca[:,pc2], projection_before[:,pc1], projection_before[:,pc2])
sh.scatterHist(target_sample_pca[:,pc1], target_sample_pca[:,pc2], projection_after[:,pc1], projection_after[:,pc2])