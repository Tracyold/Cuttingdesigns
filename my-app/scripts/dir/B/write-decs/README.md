created from the output from paramter scans it creates dec files to use

both are different renditions of the script. 

# WriteDecs.py uses a filter called 


 **is_scss_safe()** filter

- ${c.base} — JS template literals
- c.neuDark, dark ? ... — JS variable/ternary expressions
- true, false — JS booleans
- COPIES — JS constants
- Arrow functions, const, let, var

*Only clean raw CSS values like 16px, #1a82c8, rgba(0,0,0,0.5), 999px make it through.*

# writeDecs.py 

less strict filter.
