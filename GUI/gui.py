import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import time

# Create the main window
window = tk.Tk()
window.title("Voice-based AI Virtual Assistant")
window.geometry("400x300")

# Create a style for ttk elements
style = ttk.Style()
style.configure("TButton", font=("Arial", 12), foreground="white", background="#1E90FF")
style.map("TButton", foreground=[("pressed", "white"), ("active", "white")], background=[("pressed", "#4682B4"), ("active", "#4682B4")])

# Function to display a message with animation
def display_message_with_animation(message):
    for char in message:
        message_label.config(text=message_label.cget("text") + char)
        window.update()
        time.sleep(0.03)

# Function to handle button click event
def on_button_click():
    messagebox.showinfo("Button Clicked", "You clicked the button!")

# Create a message label with initial text
message_label = ttk.Label(window, text="", font=("Arial", 14))
message_label.pack(pady=20)

# Create an animated message
display_message_with_animation("Welcome to the AI Virtual Assistant")

# Create a button with a sci-fi design
button = ttk.Button(window, text="Click Me", command=on_button_click)
button.pack(pady=10)

# Create a pulsating effect on the button
scale_factor = 1.2
is_scaling_up = True

def animate_button():
    global scale_factor, is_scaling_up
    if is_scaling_up:
        scale_factor += 0.01
        if scale_factor >= 1.4:
            is_scaling_up = False
    else:
        scale_factor -= 0.01
        if scale_factor <= 1.2:
            is_scaling_up = True
    button.config(font=("Arial", int(12 * scale_factor)))
    window.after(10, animate_button)

animate_button()

# Add a background image to the window
# bg_image = tk.PhotoImage(file="background_image.png")
# background_label = tk.Label(window, image=bg_image)
# background_label.place(x=0, y=0, relwidth=1, relheight=1)

# Start the main event loop
window.mainloop()
