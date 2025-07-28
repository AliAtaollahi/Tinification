# Python script to remove newlines and concatenate lines with commas
def process_file(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Remove newlines and concatenate lines with commas
    concatenated_lines = ''.join(line.strip() for line in lines)

    # Write the result back to the file
    with open(file_path, 'w') as file:
        file.write(concatenated_lines)

# Provide the path of the file to be processed
file_path = 'tau_actions.txt'
process_file(file_path)