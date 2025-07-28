#Read the content of the file 'castfile.aut'
input_file_path = 'castfile.aut'
output_file_path = 'new_castfile.aut'

# Open the input file to read
with open(input_file_path, 'r') as input_file:
    content = input_file.read()

# Ensure the last character is ')'
if content[-1] != 't':

    content = content.rstrip()

# Write the content to the new file
with open(output_file_path, 'w') as output_file:
    output_file.write(content)

print(f"Content copied to {output_file_path} with the last character as ')'.")