rm -rf extraction_function/build/*
rm -rf castfunction_variables/build/*

#!/bin/bash

# Check if the required argument is passed
if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Error: Argument missing. Please provide the argument."
    exit 1
fi

# Create a temporary file to hold the argument
temp_file="temp1.txt"

# Write the argument passed to the temp file
echo -e "../$1" > "temp1.txt" 
cd castfunction_variables
lfc castfunction_variables.lf >/dev/null 2>&1

./bin/castfunction_variables> ../castfile.aut
echo "BRTTS generated"

cd ..
sed -i '1,2d'  castfile.aut

file="castfile.aut"

# Extract the number of states and transitions from the first line
first_line=$(head -n 1 "$file")

# Get the number of states (the first number after "NUMBER OF STATES:")
number_of_states=$(echo "$first_line" | awk -F ' ' '{print $4}')

# Get the number of transitions (the first number after "NUMBER OF TRANSITIONS:")
number_of_transitions=$(echo "$first_line" | awk -F ' ' '{print $8}')

# Prepare the new first line with the des format
new_first_line="des(0,$number_of_transitions,$number_of_states)"

# Replace the first line with the new line
sed -i "1s/.*/$new_first_line/" "$file"
python3 mender.py >/dev/null 2>&1

echo -e "../new_castfile.aut">"temp.txt"
cat "$2">>"temp.txt"
cd extraction_function


lfc extraction_function.lf >/dev/null 2>&1
./bin/extraction_function > ../tau_actions.txt
echo "LTS generated"
cd ..
sed -i '/<<\|>>/d' tau_actions.txt
python3 concat.py
tau_content=$(cat tau_actions.txt)
ltsconvert --equivalence=weak-trace --tau="$tau_content" new_castfile.aut new_castfile_tinytwin.aut
ltsconvert new_castfile_tinytwin.aut new.dot
echo "TinyTwin generated"
