import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense

def build_lstm_model(input_shape: tuple, n_classes: int) -> tf.keras.Model:
    """
    สร้างโมเดล LSTM สำหรับเรียนรู้ลำดับการเดินทาง (ปรับปรุงสำหรับ Multiclass Classification)
    """
    model = Sequential([
        # เลเยอร์ LSTM (64 nodes): ทำหน้าที่เป็นหน่วยความจำระยะยาว ช่วยจำลำดับการเดินทาง เช่น ถ้าผู้ป่วยไป [บ้าน -> ร้านค้า] สมองส่วนนี้จะคอยจำคิวก่อนหน้าไว้
        LSTM(64, input_shape=input_shape, return_sequences=False),
        # เลเยอร์ Dense (32 nodes + ReLU): เป็นเลเยอร์กลางช่วยย่อยข้อมูลและสกัดฟีเจอร์เด่นๆ
        Dense(32, activation="relu"),
        # เลเยอร์เอาต์พุต (Softmax): ทำหน้าที่แปลงผลลัพธ์ให้เป็นความน่าจะเป็นของการไปสถานที่ต่างๆ (เช่น บ้าน 80%, สวนสาธารณะ 20%)
        Dense(n_classes, activation="softmax")
    ])
    
    # loss: "sparse_categorical_crossentropy" ใช้กับข้อมูลที่มี label เป็นตัวเลข (เช่น คลาส 0, 1, 2)
    # metrics: ["accuracy"] ใช้เพื่อติดตามความแม่นยำระหว่างการเทรน
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model

def train_model(sequences: list, labels: list, n_classes: int) -> tf.keras.Model:
    """
    Train LSTM จากข้อมูลการเดินทางของผู้ป่วย
    Input  : sequences = ลำดับสถานที่, labels = จุดหมายถัดไป (คลาส ID), n_classes = จำนวนสถานที่ทั้งหมด
    Output : โมเดลที่ train แล้ว
    """
    X = np.array(sequences)
    y = np.array(labels)

    # ส่งค่า n_classes เข้าไปในฟังก์ชันสร้างโมเดลด้วย
    model = build_lstm_model(input_shape=(X.shape[1], X.shape[2]), n_classes=n_classes)
    model.fit(X, y, epochs=10, batch_size=32, verbose=0)

    return model

def predict_next_place(model, sequence: list) -> int:
    """
    ทำนายจุดหมายถัดไปของผู้ป่วย (คืนค่าเป็น ID ของสถานที่ที่มีความน่าจะเป็นสูงสุด)
    """
    X = np.array([sequence])
    predictions = model.predict(X)[0]
    
    # ใช้ np.argmax เพื่อหา index (หรือ cluster_id) ที่มีความน่าจะเป็นสูงสุด
    predicted_class = int(np.argmax(predictions))
    return predicted_class