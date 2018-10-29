import os

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
# The GPU id to use, usually either "0" or "1"
os.environ["CUDA_VISIBLE_DEVICES"] = '0'

from keras import Input, Model, regularizers
from keras.callbacks import ReduceLROnPlateau, EarlyStopping, TensorBoard, ModelCheckpoint
import numpy as np
from keras.layers import Bidirectional, LSTM, TimeDistributed, Dense, Permute, Lambda, K, RepeatVector, multiply, \
    Softmax, Multiply

from TCN import TCN_LSTM, residual_TCN_LSTM, ED_TCN, attention_TCN_LSTM, ED_TCN
from modules.utils import read_from_file, read_features, mask_data, phase_length, freeeze_attention, freeze_LSTM, \
    unfreeze_attention, unfreeze_LSTM, cal_avg_len
from sklearn.utils import class_weight

from keras.utils import multi_gpu_model

# import tensorflow as tf
# from keras.backend.tensorflow_backend import set_session
# config = tf.ConfigProto()
# config.gpu_options.per_process_gpu_memory_fraction = 0.3
# set_session(tf.Session(config=config))

local_feats_path = '/Users/seanxiang/data/cholec80/feats/'
remote_feats_path = '/home/cxia8134/dev/baseline/feats/'

model_name = 'BiLSTM-500nodes-attentionBefore-noMask-2Stages-6'

lr_reducer = ReduceLROnPlateau(factor=np.sqrt(0.1), cooldown=0, patience=7, min_lr=0.5e-6, mode='auto')
early_stopper = EarlyStopping(monitor='val_loss', min_delta=0.001, patience=10)
tensor_board = TensorBoard('log/' + model_name)
# save model if validation loss decreased
checkpointer = ModelCheckpoint(filepath='/home/cxia8134/dev/baseline/temp/{epoch:02d}-{val_loss:.2f}.hdf5',
                               verbose=1,
                               save_best_only=True)


def attention_3d_block(inputs):
    # inputs.shape = (batch_size, time_steps, input_dim)
    input_dim = int(inputs.shape[2])
    a = Permute((2, 1))(inputs)
    # a = Reshape((input_dim, TIME_STEPS))(a) # this line is not useful. It's just to know which dimension is what.
    a = Dense(max_len, activation='softmax', name='weighting')(a)
    if SINGLE_ATTENTION_VECTOR:
        a = Lambda(lambda x: K.mean(x, axis=1), name='dim_reduction')(a)
        a = RepeatVector(input_dim)(a)
    a_probs = Permute((2, 1), name='attention_vec')(a)
    output_attention_mul = multiply([inputs, a_probs], name='attention_mul')
    return output_attention_mul


def attention_block(inputs, avg_len):
    lstm = Bidirectional(LSTM(avg_len, return_sequences=True,
                              dropout=0.5,
                              recurrent_dropout=0.5,
                              kernel_regularizer=regularizers.l2(l2_norm)), name='weighting')(inputs)
    attention = TimeDistributed(Dense(1), name='attention_vec')(lstm)
    attention = Softmax(axis=1, name='attention_weighting')(attention)
    context = Multiply(name='attention_mul')([attention, lstm])
    return context


# without masking
def train_generator(X_train, Y_train, sample_weights=None):
    i = 0
    l = len(X_train)
    while True:
        if i > (l - 1):
            i = 0
        x = X_train[i]
        y = Y_train[i]
        i += 1
        if sample_weights is not None:
            yield np.expand_dims(x, axis=0), np.expand_dims(y, axis=0), np.expand_dims(sample_weights[i], axis=0)
        yield np.expand_dims(x, axis=0), np.expand_dims(y, axis=0)


def vali_generator(X_vali, Y_vali, sample_weights=None):
    i = 0
    l = len(X_vali)
    while True:
        if i > (l - 1):
            i = 0
        x = X_vali[i]
        y = Y_vali[i]
        i += 1
        if sample_weights is not None:
            yield np.expand_dims(x, axis=0), np.expand_dims(y, axis=0), np.expand_dims(sample_weights[i], axis=0)
        yield np.expand_dims(x, axis=0), np.expand_dims(y, axis=0)


n_nodes = 500
nb_epoch = 200
nb_classes = 7
batch_size = 10
conv_len = [8, 16, 32, 64, 128][2]
n_feat = 2048
max_len = 6000
l2_norm = 0.01
SINGLE_ATTENTION_VECTOR = False

path = remote_feats_path

X_train, Y_train = read_features(path, 'train')
X_vali, Y_vali = read_features(path, 'vali')

# TODO: append frame id to feature

# X_train_m, Y_train_, M_train = mask_data(X_train, Y_train, max_len, mask_value=-1)
# X_vali_m, Y_vali_, M_vali = mask_data(X_vali, Y_vali, max_len, mask_value=-1)


# find the average length of the training samples
avg_len = cal_avg_len(X_train)

inputs = Input(shape=(None, n_feat))

model = attention_block(inputs, avg_len)

model = Bidirectional(LSTM(n_nodes,
                           return_sequences=True,
                           input_shape=(batch_size, None, n_feat),
                           dropout=0.5,
                           name='bilstm',
                           recurrent_dropout=0.25))(model)


# Output FC layer
model = TimeDistributed(Dense(nb_classes, activation="softmax"))(model)

model = Model(inputs=inputs, outputs=model)
# model = multi_gpu_model(model, gpus=2)

# learn segmentation
model = freeeze_attention(model)

model.compile(loss='categorical_crossentropy',
              optimizer='adam',
              sample_weight_mode="temporal",
              metrics=['accuracy'])
model.summary()

# train on videos with sample weighting
model.fit_generator(train_generator(X_train, Y_train),
                    verbose=1,
                    epochs=nb_epoch,
                    steps_per_epoch=50,
                    validation_steps=10,
                    validation_data=vali_generator(X_vali, Y_vali),
                    callbacks=[lr_reducer, early_stopper, tensor_board, checkpointer])

# learn attention
model = unfreeze_attention(model)
model = freeze_LSTM(model)

model.compile(loss='categorical_crossentropy',
              optimizer='adam',
              sample_weight_mode="temporal",
              metrics=['accuracy'])
model.summary()

model.fit_generator(train_generator(X_train, Y_train),
                    verbose=1,
                    epochs=nb_epoch,
                    steps_per_epoch=50,
                    validation_steps=10,
                    validation_data=vali_generator(X_vali, Y_vali),
                    callbacks=[lr_reducer, early_stopper, tensor_board, checkpointer])

model.save('trained/' + model_name + '.h5')
