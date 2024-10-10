from chatterbot import ChatBot
from chatterbot.trainers import ChatterBotCorpusTrainer

# Create a new instance of the ChatBot
chatbot = ChatBot('MyChatBot')

# Create a new trainer for the chatbot
trainer = ChatterBotCorpusTrainer(chatbot)

# Train the chatbot based on the English corpus
trainer.train("chatterbot.corpus.english")

# Get a response from the chatbot
response = chatbot.get_response('Hello, how are you?')

print(response)
