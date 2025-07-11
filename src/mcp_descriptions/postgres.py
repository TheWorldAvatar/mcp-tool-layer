POSTGRES_UPLOAD_DESCRIPTION = """
    Upload a CSV file to a validation PostgreSQL database. This function is only for validating OBDA files.

    Uploading the CSV file to the validation database does not mean the data is integrated into the semantic stack. 

    IMPORTANT: In no circumstances, you should upload any other files to the validation database except for csv files. 
"""

POSTGRES_UPLOAD_DESCRIPTION_EXECUTION = """
Upload a CSV file to a validation PostgreSQL database. This function is only for and necessary for validating OBDA files. 


Mandatory Prerequisites:
    - Before this function, data must be converted to one or more csv files. The tool will not accept any other formats of data.  
    - In some cases, multiple csv files will be uploaded to the postgres databases into different tables. 
"""