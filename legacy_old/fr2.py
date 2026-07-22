import cv2
import numpy as np
import os
import speech_recognition as sr

def load_images(img_dir):
    """
    Load images from directory and extract features
    """
    img_paths = [os.path.join(img_dir, f) for f in os.listdir(img_dir)]

    face_recognizer = cv2.face.LBPHFaceRecognizer_create()
    faces = []
    labels = []
    for i, img_path in enumerate(img_paths):
        img = cv2.imread(img_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        face = cv2.CascadeClassifier('haarcascade_frontalface_default.xml').detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)
        for (x, y, w, h) in face:
            faces.append(gray[y:y+h, x:x+w])
            labels.append(i)
    face_recognizer.train(faces, np.array(labels))
    
    return img_paths, face_recognizer

