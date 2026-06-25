"""Shared LSTM predictor used by Module 1 (sequence learning) and Module 2 (destination prediction).
Features per timestep: [cluster_id_norm, hour_norm, day_norm, familiarity_norm]
"""
import os
import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.utils import to_categorical

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")


class SequencePredictor:
    def __init__(self, sequence_length: int = 3, n_clusters: int = 10):
        self.model = None
        self.sequence_length = sequence_length
        self.n_clusters = n_clusters

    def build_model(self, n_features: int = 4):
        model = Sequential([
            LSTM(64, input_shape=(self.sequence_length, n_features), return_sequences=True),
            Dropout(0.2),
            LSTM(32, return_sequences=False),
            Dropout(0.2),
            Dense(32, activation='relu'),
            Dense(self.n_clusters, activation='softmax')
        ])
        model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
        self.model = model
        return model

    def _build_sequence(self, recent_clusters: list[int], current_cluster: int,
                        hour: float, day: float, familiarity: float) -> list:
        padded = ([-1] * self.sequence_length + recent_clusters)[-self.sequence_length:]
        sequence = []
        for i, c in enumerate(padded):
            loc = c if i < self.sequence_length - 1 else current_cluster
            sequence.append([
                max(loc, 0) / max(self.n_clusters, 1),
                hour / 24.0,
                day / 7.0,
                familiarity
            ])
        return sequence

    def train(self, sequences: list, labels: list, epochs: int = 50,
              batch_size: int = 4, verbose: int = 0):
        X = np.array(sequences)
        y = to_categorical(np.array(labels), num_classes=self.n_clusters)
        self.build_model(n_features=X.shape[2])
        self.model.fit(X, y, epochs=epochs, batch_size=batch_size, verbose=verbose)
        return self.model

    def predict(self, sequence: list) -> tuple[int, float, list]:
        X = np.array([sequence])
        probs = self.model.predict(X, verbose=0)[0]
        predicted_idx = int(np.argmax(probs))
        confidence = float(probs[predicted_idx])
        top3_idx = np.argsort(probs)[::-1][:3]
        top3 = [(int(i), float(probs[i])) for i in top3_idx]
        return predicted_idx, confidence, top3

    def save(self, patient_id: int):
        os.makedirs(MODELS_DIR, exist_ok=True)
        path = os.path.join(MODELS_DIR, f"lstm_patient_{patient_id}.h5")
        if self.model:
            self.model.save(path)

    def load(self, patient_id: int) -> bool:
        path = os.path.join(MODELS_DIR, f"lstm_patient_{patient_id}.h5")
        if os.path.exists(path):
            self.model = load_model(path)
            self.n_clusters = self.model.output_shape[-1]
            return True
        return False
