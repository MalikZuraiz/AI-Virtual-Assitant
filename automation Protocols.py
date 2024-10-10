from keyboard import *
import webbrowser as web
import datetime
import win32com.client as win32
import os
import click
from time import sleep

def Chrome(command):

    query = str(command)

    if 'new tab' in query:

        press_and_release('ctrl + t')

    elif 'close tab' in query:

        press_and_release('ctrl + w')

    elif 'new window' in query:

        press_and_release('ctrl + n')

    elif 'history' in query:

        press_and_release('ctrl + h')

    elif 'download' in query:

        press_and_release('ctrl + j')

    elif 'bookmark' in query:

        press_and_release('ctrl + d')

        press('enter')

    elif 'incognito' in query:

        press_and_release('Ctrl + Shift + n')

    elif 'switch tab' in query:

        tab = query.replace("switch tab ", "")
        Tab = tab.replace("to","")
        
        num = Tab

        bb = f'ctrl + {num}'

        press_and_release(bb)

    elif 'open' in query:

        name = query.replace("open ","")

        NameA = str(name)

        if 'youtube' in NameA:

            web.open("https://www.youtube.com/")

        elif 'instagram' in NameA:

            web.open("https://www.instagram.com/")

        else:

            string = "https://www." + NameA + ".com"

            string_2 = string.replace(" ","")

            web.open(string_2)

def YouTube(command):

    query = str(command)

    if 'pause' in query:

        press('space bar')

    elif 'resume' in query:

        press('space bar')

    elif 'full screen' in query:

        press('f')

    elif 'film screen' in query:

        press('t')

    elif 'skip' in query:

        press('Tab + Enter')

    elif 'back' in query:

        press('j')

    elif 'increase' in query:

        press_and_release('SHIFT + .')

    elif 'decrease' in query:

        press_and_release('SHIFT + ,')

    elif 'previous' in query:

        press_and_release('SHIFT + p')

    elif 'next' in query:

        press_and_release('SHIFT + n')
    
    elif 'search' in query:

        click(x=667, y=146)

        s.say("What To Search Sir ?")

        search = s.listen()

        write(search)

        sleep(0.8)

        press('enter')

    elif 'mute' in query:

        press('m')

    elif 'unmute' in query:

        press('m')

    elif 'my channel' in query:

        web.open("https://www.youtube.com/channel/UC7A5u12yVIZaCO_uXnNhc5g")

    else:
        s.say("No Command Found!")

def Windows(command):

    query = str(command)

    if 'home screen' in query:

        press_and_release('windows + m')

    elif 'minimize' in query:

        press_and_release('windows + m')

    elif 'show start' in query:

        press('windows')

    elif 'open setting' in query:

        press_and_release('windows + i')

    elif 'open search' in query:

        press_and_release('windows + s')

    elif 'screen shot' in query:

        press_and_release('windows + SHIFT + s')

    elif 'restore windows' in  query:

        press_and_release('Windows + Shift + M')

    else:
        s.say("Sorry , No Command Found!")

def Notepad():

    s.say("Tell Me The Query .")
    s.say("I Am Ready To Write .")

    writes = s.listen()

    time = datetime.now().strftime("%H:%M")

    filename = str(time).replace(":","-") + "-note.txt"

    with open(filename,"w") as file:

        file.write(writes)

    path_1 = "E:\\Y O U T U B E\\J A R V I S  S E R I E S\\H O W  T O  M A K E  J A R V I S\\" + str(filename)

    path_2 = "E:\\Y O U T U B E\\J A R V I S  S E R I E S\\H O W  T O  M A K E  J A R V I S\\DataBase\\NotePad\\" + str(filename)

    os.rename(path_1,path_2)

    os.startfile(path_2)

def CloseNotepad():

    os.system("TASKKILL /F /im Notepad.exe")

def WhatsappMsg(name,message):
     
    os.startfile("C:\\Users\\hp\\AppData\\Local\\WhatsApp\\WhatsApp.exe")

    sleep(10)

    click(x=271, y=112)

    sleep(1)

    write(name)

    sleep(1)

    click(x=229, y=274)

    sleep(1)

    click(x=594, y=694)

    sleep(1)

    write(message)

    press('enter')

def WhatsappCall(name):

    os.startfile("C:\\Users\\hp\\AppData\\Local\\WhatsApp\\WhatsApp.exe")

    sleep(10)

    click(x=271, y=112)

    sleep(1)

    write(name)

    sleep(1)

    click(x=229, y=274)

    sleep(1)

    click(x=594, y=694)

    sleep(1)

    click(x=1216, y=65)

def WhatsappChat(name):

    os.startfile("C:\\Users\\hp\\AppData\\Local\\WhatsApp\\WhatsApp.exe")

    sleep(10)

    click(x=271, y=112)

    sleep(1)

    write(name)

    sleep(1)

    click(x=229, y=274)

    sleep(1)

    click(x=594, y=694)

    sleep(1)


def word():
    # Initialize the Word application
    word = win32.gencache.EnsureDispatch('Word.Application')
    word.Visible = True

    # Define voice commands
    commands = {
        'new file': lambda: word.Documents.Add(),
        'save as': lambda: word.ActiveDocument.SaveAs2(),
        'rename': lambda: word.ActiveDocument.SaveAs2('new_filename.docx'),
        'bold': lambda: word.Selection.Font.Bold == True,
        'italic': lambda: word.Selection.Font.Italic == True,
        'bullets': lambda: word.Selection.Range.ListFormat.ApplyBulletDefault(),
        'format': lambda: setattr(word.Selection.Font, 'Name', 'Calibri')
    }

    # Continuously listen for voice commands
    while True:
        with sr.Microphone() as source:
            print('Listening...')
            audio = r.listen(source)

        # Recognize the spoken command
        try:
            command = r.recognize_google(audio)
            print('Command:', command)

            # Execute the command if it's in the commands dictionary
            if command in commands:
                commands[command]()

        except sr.UnknownValueError:
            print('Sorry, I did not understand that.')
        except sr.RequestError:
            print('Sorry, I could not process your request.')

        
        
            
        