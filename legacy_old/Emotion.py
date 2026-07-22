import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import SVC
from sklearn.metrics import classification_report

# Step 1: Data Exploration
with open('emotion.txt', 'r') as file:
    lines = file.readlines()

data = []
for line in lines:
    text, emotion = line.strip().split(';')
    data.append([text, emotion])

# Convert data to pandas DataFrame
data = pd.DataFrame(data, columns=['Text', 'Emotion'])

# Step 2: Preprocessing
# (You can add additional preprocessing steps as per your requirements)

# Step 3: Feature Extraction
vectorizer = TfidfVectorizer()
X = vectorizer.fit_transform(data['Text'])
y = data['Emotion']

# Step 4: Model Training
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

svm = SVC(kernel='linear')
svm.fit(X_train, y_train)

# Step 5: Model Evaluation
y_pred = svm.predict(X_test)
print(classification_report(y_test, y_pred))

# Step 6: Deployment
# You can now use this trained model for real-time emotion recognition

# Example usage:
input_text = "it hurts me"
input_vector = vectorizer.transform([input_text])
predicted_emotion = svm.predict(input_vector)
print("Predicted Emotion:", predicted_emotion[0])
