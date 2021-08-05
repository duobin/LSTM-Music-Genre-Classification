#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    PyTorch lightning implementation of a simple 2-layer-deep LSTM for genre classification of musical audio.
    Feeding the LSTM stack are spectral {centroid, contrast}, chromagram & MFCC features (33 total values)

    We use this toy dataset and a well-understood, "easy" task to learn PTL based on the original PyTorch impl

"""

import os

import pytorch_lightning as pl
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch.utils.data as data
from torch.utils.data import DataLoader

from GenreFeatureData import (
    GenreFeatureData,
)  # local python class with Audio feature extraction (librosa)


# class definition
class LSTM(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim=8, num_layers=2):
        super(LSTM, self).__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers

        # setup LSTM layer
        self.lstm = nn.LSTM(self.input_dim, self.hidden_dim, self.num_layers)

        # setup output layer
        self.linear = nn.Linear(self.hidden_dim, output_dim)
        if torch.cuda.is_available():
            print("\nTraining on GPU")
        else:
            print("\nNo GPU, training on CPU")

    def forward(self, input, hidden=None):
        # lstm step => then ONLY take the sequence's final timetep to pass into the linear/dense layer
        # Note: lstm_out contains outputs for every step of the sequence we are looping over (for BPTT)
        # but we just need the output of the last step of the sequence, aka lstm_out[-1]
        lstm_out, hidden = self.lstm(input, hidden)
        logits = self.linear(lstm_out[-1])
        genre_scores = F.log_softmax(logits, dim=1)
        return genre_scores, hidden


class MusicGenreClassifer(pl.LightningModule):

    def __init__(self, batch_size):
        super().__init__()
        self.model = LSTM(input_dim=33, hidden_dim=128, output_dim=8, num_layers=2)
        self.hidden = None

        # To keep LSTM stateful between batches, you can set stateful = True, which is not suggested for training
        self.stateful = False

    def forward(self, x, hidden=None):
        prediction, self.hidden = self.model(x, hidden)
        return prediction

    def training_step(self, batch, batch_idx):
        X_local_minibatch, y_local_minibatch = batch            # Unpack input
        X_local_minibatch = X_local_minibatch.permute(1, 0, 2)  # Reshape input for loss_function

        y_pred, self.hidden = self.model(X_local_minibatch, self.hidden)  # forward pass
        if not self.do_continue_train:
            self.hidden = None
        else:
            h_0, c_0 = self.hidden
            h_0.detach_(), c_0.detach_()
            self.hidden = (h_0, c_0)

        y_local_minibatch = torch.max(y_local_minibatch, 1)[1]  # NLLLoss expects class indices
        loss = nn.NLLLoss(y_pred, y_local_minibatch)            # compute loss
        self.log("train_loss", loss)
        return loss

    def validation_step(self, batch, batch_idx):
        X_local_minibatch, y_local_minibatch = batch            # Unpack input
        X_local_minibatch = X_local_minibatch.permute(1, 0, 2)  # Reshape input for loss_function
        y_pred, self.hidden = self.model(X_local_minibatch, self.hidden)
        if not self.stateful:
            self.hidden = None

        y_local_minibatch = torch.max(y_local_minibatch, 1)[1]  # NLLLoss expects class indices
        val_loss = nn.NLLLoss(y_pred, y_local_minibatch)        # compute loss
        return val_loss

    def configure_optimizers(self):
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)
        return optimizer


class GTZANDataset(data.Dataset):

    def __init__(self, partition):
        self.partition = partition
        self.genre_features = GenreFeatureData()

        # if all of the preprocessed files do not exist, regenerate them all for self-consistency
        if (
                os.path.isfile(self.genre_features.train_X_preprocessed_data)
                and os.path.isfile(self.genre_features.train_Y_preprocessed_data)
                and os.path.isfile(self.genre_features.dev_X_preprocessed_data)
                and os.path.isfile(self.genre_features.dev_Y_preprocessed_data)
                and os.path.isfile(self.genre_features.test_X_preprocessed_data)
                and os.path.isfile(self.genre_features.test_Y_preprocessed_data)
        ):
            print("Preprocessed files exist, deserializing npy files")
            self.genre_features.load_deserialize_data()
        else:
            print("Preprocessing raw audio files")
            self.genre_features.load_preprocess_data()

        self.train_X = torch.from_numpy(self.genre_features.train_X).type(torch.Tensor)
        self.dev_X = torch.from_numpy(self.genre_features.dev_X).type(torch.Tensor)
        self.test_X = torch.from_numpy(self.genre_features.test_X).type(torch.Tensor)

        # Targets is a long tensor of size (N,) which tells the true class of the sample.
        self.train_Y = torch.from_numpy(self.genre_features.train_Y).type(torch.LongTensor)
        self.dev_Y = torch.from_numpy(self.genre_features.dev_Y).type(torch.LongTensor)
        self.test_Y = torch.from_numpy(self.genre_features.test_Y).type(torch.LongTensor)

        # Convert {training, test} torch.Tensors
        print("Training X shape: " + str(self.genre_features.train_X.shape))
        print("Training Y shape: " + str(self.genre_features.train_Y.shape))
        print("Validation X shape: " + str(self.genre_features.dev_X.shape))
        print("Validation Y shape: " + str(self.genre_features.dev_Y.shape))
        print("Test X shape: " + str(self.genre_features.test_X.shape))
        print("Test Y shape: " + str(self.genre_features.test_Y.shape))

    def __getitem__(self, index):
        # train_X shape: (total # of training examples, sequence_length, input_dim)
        # train_Y shape: (total # of training examples, # output classes)
        X_training_example_at_index, y_training_example_at_index = None, None
        if self.partition == 'train':
            X_training_example_at_index = self.train_X[index, ]
            y_training_example_at_index = self.train_Y[index, ]
            # torch.index_select(self.train_X, dim=0, index=torch.tensor([index], dtype=torch.long))

            # # Reshape input & targets to "match" what the loss_function wants
            # X_training_example_at_index = X_training_example_at_index.permute(1, 0, 2)
            # # NLLLoss does not expect a one-hot encoded vector as the target, but class indices
            # y_training_example_at_index = torch.max(y_training_example_at_index, 1)[1]

        elif self.partition == 'dev':
            X_training_example_at_index = self.dev_X[index, ]
            y_training_example_at_index = self.dev_Y[index, ]

        elif self.partition == 'test':
            X_training_example_at_index = self.test_X[index, ]
            y_training_example_at_index = self.test_Y[index, ]

        return X_training_example_at_index, y_training_example_at_index

    def __len__(self):
        if self.partition == 'train':
            return len(self.train_Y)
        elif self.partition == 'dev':
            return len(self.dev_Y)
        elif self.partition == 'test':
            return len(self.test_Y)


# class MusicGenreDataModule(pl.LightningDataModule):
#     def __init__(self, batch_size=35):
#         super().__init__()
#         self.batch_size = batch_size # num of training examples per minibatch
#
#     def prepare_data(self):
#
#
#     def setup(self, stage: Optional[str] = None):
#
#
#
#         # IOHAVOC
#         self.train_dataset = None
#         self.val_dataset = None
#         self.test_dataset = None
#         self.train_dims = self.train_dataset.next_batch.size()
#
#     def train_dataloader(self) -> DataLoader:
#         return DataLoader(self.train_dataset, batch_size=self.batch_size)
#
#     def val_dataloader(self) -> DataLoader:
#         return DataLoader(self.val_dataset, batch_size=self.batch_size)
#
#     def test_dataloader(self) -> DataLoader:
#         return DataLoader(self.val_dataset, batch_size=self.batch_size)


if __name__ == "__main__":
    # model = MusicGenreClassifer()
    # trainer = pl.Trainer()
    #
    # # genre_dm = MusicGenreDataModule()
    #
    # # trainer.fit(model, train_loader, val_loader)
    # trainer.fit(model, genre_dm)

    train_dataset = GTZANDataset('train')
    dev_dataset = GTZANDataset('dev')
    # debug
    # print(dataset[0:10])

    train_dataloader = DataLoader(train_dataset, batch_size=35, shuffle=False)
    dev_dataloader = DataLoader(dev_dataset, batch_size=35, shuffle=False)

    for i, batch in enumerate(train_dataloader):
        print(i, batch)

    for i, batch in enumerate(dev_dataloader):
        print(i, batch)
