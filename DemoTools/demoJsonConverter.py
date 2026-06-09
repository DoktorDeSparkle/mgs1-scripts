import json, argparse

workingFile = "build-proprietary/demo/testOutput.json"

def convertToNew(oldData: dict) -> dict: # Returns newData format
    newData = {}
    for demo in oldData.keys():
        newData[demo] = {}
        for key in oldData[demo][0].keys():
            timing, duration = oldData[demo][1][key].split(",")
            # print(f"Timing: {timing}, Duration: {duration}, Text: {oldData[demo][0][key]}")
            newData[demo][timing] = {
                "duration": str(duration),
                "text": oldData[demo][0][key]
            }
    
    return newData

"""
A bit of an oversight, but since we've changed formats for the demo json, we need to convert it BACK. 
"""

def convertToOld(newData: dict) -> dict: # Returns oldData format
    oldData = {}
    for demoName in newData.keys(): 
        i = 1
        texts = {}
        timings = {}
        for startFrame in newData[demoName].keys():
            iFormat = f"{i:02}"
            texts.update( {iFormat : newData[demoName][startFrame]["text"]})
            timings.update( {iFormat: f"{startFrame},{newData[demoName][startFrame]["duration"]}"})
            oldData[demoName] = [texts, timings]
            i += 1 
        # print(oldData[demoName])
    return oldData

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f'Convert a demo dialogue JSON between v1 and v2. CAN OVERWRITE IF NO OUTPUT GIVEN!')
    parser.add_argument("input", type=str, help="File to convert")
    parser.add_argument("output", type=str, nargs="?", default=None, help="Output filename. If none is provided, overwrites original file.")
    parser.add_argument("--force", "-f", action="store_true", help="Force overwrite original file.")
    args = parser.parse_args()

    workingFile = args.input
    forceOverwrite: bool = args.force
    print(forceOverwrite)

    inputData = json.load(open(workingFile, 'r'))
    newData = {}
    if args.output == None:
        outputFile = workingFile
        if not forceOverwrite:
            confirm = input(f"WARNING! This will overwrite the original file! Continue? >")
            if confirm.lower() not in ["y", "yes"]:
                print(f"NOT CONFIRMED! Use y, yes to confirm.")
                exit(1)
    else:
        outputFile = args.output
    
    # If the first demo is a list, it's v1 and we convert to v2. 
    # Else if it's a dict, we convert back to v1.
    firstEntry = next(iter(inputData.values()))
    if isinstance(firstEntry, list):
        # Convert to new format
        print(f"Type is {type(firstEntry)}, convert to v2...")
        outputData = convertToNew(inputData)
    elif isinstance(firstEntry, dict):
        print(f"Type is {type(firstEntry)}, convert to v1...")
        # Convert to old format
        outputData = convertToOld(inputData)
    else:
        print(f"Error! Unknown file schema. Type is {type(firstEntry)}!")
        exit(2)
    
    # Write the file back to the same filename.
    with open(outputFile, 'w') as f:
        json.dump(outputData, f, ensure_ascii=False)