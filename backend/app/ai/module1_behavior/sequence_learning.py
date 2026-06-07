# pathguard/backend/app/ai/module1_behavior/sequence_learning.py

import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense

def build_lstm_model(input_shape: tuple) -> tf.keras.Model:
    """
    สร้างโมเดล LSTM สำหรับเรียนรู้ลำดับการเดินทาง
    """
    model = Sequential([
        LSTM(64, input_shape=input_shape, return_sequences=False),
        Dense(32, activation="relu"),
        Dense(1, activation="sigmoid")
    ])
    model.compile(optimizer="adam", loss="mse")
    return model

def train_model(sequences: list, labels: list) -> tf.keras.Model:
    """
    Train LSTM จากข้อมูลการเดินทางของผู้ป่วย
    Input  : sequences = ลำดับสถานที่, labels = จุดหมายถัดไป
    Output : โมเดลที่ train แล้ว
    """
    X = np.array(sequences)
    y = np.array(labels)

    model = build_lstm_model(input_shape=(X.shape[1], X.shape[2]))
    model.fit(X, y, epochs=10, batch_size=32, verbose=0)

    return model

def predict_next_place(model, sequence: list) -> float:
    """
    ทำนายจุดหมายถัดไปของผู้ป่วย
    """
    X = np.array([sequence])
    return model.predict(X)[0][0]