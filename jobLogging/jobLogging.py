import cups
import codecs
import datetime
import re
import ConfigParser
from tabulate import tabulate
import time
import logging
import os

conn = cups.Connection()
printers = conn.getPrinters()
conf = ConfigParser.ConfigParser()
confFile = "./printers.conf"
conf.read(confFile)

def get_jobs():
  """
  Create dict of jobs per printer, with user info per job
  """
  printersToLog = conf.sections()
  printerNames = {}
  printers = conn.getPrinters()
  jobDict = {}
  for name in printersToLog: # create dict of printers by their URI
    if name in printers:
      printerURI = printers[name]["printer-uri-supported"]
      printerNames[printerURI] = name
      jobDict[name] = {}

  completedJobs = conn.getJobs('completed')
  attributes = ["time-at-processing", "job-name", "job-media-sheets-completed", "printer-uri"]

  for id in completedJobs:
    data = conn.getJobAttributes(id, attributes)
    printerURI = data["printer-uri"]
    printerName = printerURI.split('/')
    printerName = printerName[-1]
    if printerName in printers:
      jobname = data['job-name']
      username = re.search('\[([^\]]*)\]', jobname)
      if username:
        jobname = jobname.replace(username.group(0),'')
        username = username.group(1)
      else:
        username = "unknown"
      job = {
        'jobname': jobname,
        'user': username,
        'pages': data["job-media-sheets-completed"],
        'date': data["time-at-processing"]
      }
      jobDict[printerName][id] = job
  return jobDict

def get_user_log (jobDict, printerName, startDate=0):
  """
  Create dict of users with total jobs per printer
  """
  userLog = {}
  printerSettings = ConfigSectionMap(printerName)
  prijs = printerSettings["price"]
  try:
    startDate = time.mktime(datetime.datetime.strptime(startDate, "%d/%m/%Y").timetuple())
  except Exception:
    print("Wrong date input")
  for job in jobDict[printerName]:
    job = jobDict[printerName][job]
    if job["date"] > startDate:
      if job["user"] not in userLog:
        userLog[job["user"]] = {"paginas": 0}
      pages = userLog[job["user"]]["paginas"] + job["pages"]
      userLog[job["user"]] = {
        "paginas": pages,
        "kosten": pages * float(prijs)
      }
  return userLog

def ConfigSectionMap(section):
    dict1 = {}
    options = ["price", "sender", "receivers", "custom", "lastDate"]
    for option in options:
        try:
            dict1[option] = conf.get(section, option)
            if dict1[option] == -1:
                print("skip: %s" % option)
            #if option is "receivers":
                # dict1[option] = dict1[option].value.split(',')
        except:
            print("no option %s found" % option)
            dict1[option] = None
    return dict1

def create_print_table(printerName, jobLog):
    """
    This function creates a printable table containing the log
    """
    options = ConfigSectionMap(printerName) # get printer settings
    userLog = get_user_log(jobLog, printerName, options["lastDate"])
    userList = []
    for name in userLog:
        user = userLog[name]
        value = [name, user["paginas"], user["kosten"]]
        userList.append(value)
    table = tabulate(userList,
            headers=["User", "Pages", "Price"], 
            tablefmt="fancy_grid",
            floatfmt=".2f")
    return table

def write_to_file(table):
    filename = "./logs/printLog " + time.strftime("%d-%m-%Y") + ".txt"
    if not os.path.exists(os.path.dirname(filename)):
        try:
            os.makedirs(os.path.dirname(filename))
        except OSError: # Guard against race condition
            if not os.path.isdir(filename):
                raise
    file = codecs.open(filename, "w+", "utf-8")
    file.write(table)
    file.close()

def main():
  jobLog = get_jobs() # get all the jobs in a dict format
  for printerName in conf.sections(): # loop over every registered printer
    #TODO make sure all options are set
    printTable = create_print_table(printerName, jobLog)
    print(printTable)
    write_to_file(printTable)
    # set new last date since log was sent
    curDate = time.strftime("%d/%m/%Y")
    conf.set(printerName, "lastDate", curDate)
    with open(confFile, 'wb') as configfile:
        conf.write(configfile)

main()
