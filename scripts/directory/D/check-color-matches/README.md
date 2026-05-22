colorsonly.py

scans all loc files against the tokens and colors file to find unused variables inside of class names, and where names are used and not declared.


the fix simply add "$color- to the start of each variable that that uses a custom color call. make sure your colors file is names "color-" inside of tokens for this to work.

