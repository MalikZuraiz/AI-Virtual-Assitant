from chatbot import say , image , chatgpt_codeGeneration, intro, startup, SystemSpecifications,temprature, play_music, generate_responses, listen
import os
import datetime

def Command(query):

    if "bye" in query or "goodbye" in query:
        exit()

    elif "what is the time"in query or "what's the time" in query or "what time is it" in query:
        strTime = datetime.datetime.now().strftime("%H:%M:%S")
        say(f"Sir, the time is {strTime}")

    elif "generate image"in query  or "image generation" in query:
        image(query)

    elif "generate Code" in query or"code generation" in query:
        chatgpt_codeGeneration(query)

    elif "give me intro"in query or "introduce yourself" in query:
        intro()

    elif "complete system details"in query or "all system details"in query or "complete system specification" in query:
        startup()

    elif "system details"in query or "system specification" in query:
        SystemSpecifications()

    elif "system usage" in query or "usage of system" in query:
        #systemUsage()
        pass

    elif "temperature in" in query or "temperature of" in query:
        query=query.replace("temperature in","")
        temprature(query)

    elif "shutdown pc"in query or "shutdown my pc"in query or "pc shutdown" in query:
        os.system("shutdown /s")

    elif "restart pc"in query or "restart my pc"in query or "pc restart" in query:
        os.system("shutdown /r")

    elif "play" in query:
        query=query.replace("play","")
        play_music(query)

    elif "go to sleep" in query or "go offline" in query or "bye" in query or "goodbye" in query:
        exit()
  
    else:
        say(generate_responses(query))



query=listen()
Command(query)