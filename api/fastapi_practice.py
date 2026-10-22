from fastapi import FastAPI 

app=FastAPI()

@app.post("/users")

def create_user(name:str, roll_no:int):
    print("nn")
    return{name:name, roll_no:roll_no}

create_user("mmi68",12)

