#!/bin/bash
# AutoLoader Startup Script

clear

# Check if running from live USB
if grep -q "archiso" /proc/cmdline; then
    echo "Running from Libre_Core Live USB Environment..."
else
    echo "Running in standard environment..."
fi

# Ensure Python dependencies are available
# In a real environment, they might be installed globally via pacman 
# (e.g. python-textual, python-rich, etc as listed in packages.x86_64)

cd /opt/autoloader

# Try to launch the Python TUI app
echo "Starting Libre_Core AutoLoader..."
python -m autoloader run > /dev/null 2>&1

if [ $? -ne 0 ]; then
    echo "Textual UI failed to load. Falling back to dialog menu..."
    
    # Simple fallback dialog
    dialog --title "Libre_Core AutoLoader - Fallback Mode" \
           --msgbox "The main graphical interface failed to start.\nYou will be dropped to a root shell.\n\nYou can manually run tools like flashrom from here." 10 60
    
    # Drop to shell
    exec /bin/bash
fi

# If user exits the TUI, drop them to shell or reboot
dialog --title "AutoLoader Exited" \
       --yesno "Do you want to reboot now? (Select No to drop to root shell)" 8 50

if [ $? -eq 0 ]; then
    reboot
else
    clear
    echo "Type 'reboot' when you are done."
    exec /bin/bash
fi
