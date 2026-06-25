"""Sequence learning — thin wrapper around shared LSTM for Module 1.
Trains on GPS history sequences to predict next place cluster.
"""
from app.ai.lstm_utils import SequencePredictor


def build_lstm_model(input_shape: tuple, n_classes: int):
    predictor = SequencePredictor(sequence_length=input_shape[0], n_clusters=n_classes)
    predictor.build_model(n_features=input_shape[2])
    return predictor.model


def train_model(sequences: list, labels: list, n_classes: int) -> SequencePredictor:
    seq_len = len(sequences[0]) if sequences else 3
    predictor = SequencePredictor(sequence_length=seq_len, n_clusters=n_classes)
    predictor.train(sequences, labels, epochs=10, batch_size=32)
    return predictor


def predict_next_place(predictor: SequencePredictor, sequence: list) -> int:
    cluster_id, confidence, _ = predictor.predict(sequence)
    return cluster_id
