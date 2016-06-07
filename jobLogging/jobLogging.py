import cups
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

# create dict of jobs per printer, with user info per job
def get_jobs():
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
    if printerURI in printerNames:
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
      jobDict[printerNames[printerURI]][id] = job

  # print(json.dumps(jobDict, indent = 4))
  return jobDict

# create dict of users with total jobs per printer
def get_user_log (jobDict, printerName, startDate=0):
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

def manage_mail_logs(cpp):
    # if printer to track matches printer name:
    #   if day of month to print matches, send mail
    conf = ConfigParser.ConfigParser()
    sendToday = False
    while True:
        conf.read("printers.conf")
        printers = cpp.get_printers() # use all registered printers
        try:
            for printer in printers:
                if printer.name in conf.sections():
                    opt = ConfigSectionMap(conf, printer.name)
                    # today is one of the days of the month
                    if opt['days'] is None:
                        days = 28
                    else:
                        days = int(opt['days'])
                    if datetime.datetime.today().day is days:
                        if not sendToday:
                            printer.send_mail(opt['price'], opt['sender'], opt['receivers'], opt['custom'])
                            sendToday = True
                    else:
                        sendToday = False
        except Exception:
            logging.exception("oh snap, email exception")

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

def send_mail(self, price, sender, receivers, custom):
    if receivers is None or receivers is "":
        receivers = []
    if custom is None:
        custom = ""
    self.cpp.email_print_log(self.id, self.name, price, sender, receivers, custom)

def email_print_log(self, printer_id, printerName, price, sender, receivers, custom):
    #TODO store logs (maybe sqlite) and webinterface with django
    #TODO perform logging in seperate script (no more threading and use cron)

    docs = self.auth.session.post(
        PRINT_CLOUD_URL + 'jobs',
        {
            'output': 'json',
            'printerid': printer_id,
        },
    ).json()
    jobDict = {}
    for job in docs['jobs']:
        if job["status"] == "DONE": # check if done
            user = job['ownerId']
            price = float(price)
            pages = job['numberOfPages']
            newPages = 0
            printTime = datetime.datetime.fromtimestamp(int(job['updateTime'])/1000)
            if  printTime > datetime.datetime.now()-relativedelta(month=+1): # time since last send day
                newPages = pages
            if not user in jobDict:
                jobDict[user] = [pages, newPages, newPages * price]
            else:
                tmpDict = jobDict[user]
                jobDict[user] = [tmpDict[0] + pages, tmpDict[1] + newPages,
                                 (tmpDict[1] + newPages) * price]
    userList = []
    for key, value in jobDict.iteritems():
        value.insert(0, key)
        userList.append(value)
    printTable = tabulate(userList, headers=["User", "Total pages", "This month", "Price"])
    if receivers == []: # default to user emails
        receivers = [row[0] for row in userList]
    else:
        receivers = receivers.split(',')
    message = ("From: PrintServer <" + sender + " >\n"
                "To: <" + ", ".join(receivers) + ">\n"
                "Subject: Prints this month for printer '" + printerName + "'\n \n"
                + custom + "\n \n"
               "These are the prints for the month: \n \n"
            )
    message += printTable
    logging.info("Trying to send: \n" + message)
    try:
       smtpObj = smtplib.SMTP('localhost')
       smtpObj.sendmail(sender, receivers, message)
       logging.info("Successfully sent email")
    except Exception:
       logging.info("Error: unable to send email")
    logging.info("Writing log to file")
    # print to file
    filename = "logs/printlog_" + time.strftime("%d-%m-%Y") + ".txt"
    dir = os.path.dirname(filename)
    if not os.path.exists(dir):
        os.makedirs(dir)
    file = open(filename, 'w')
    file.write(message)
    file.close()

def create_print_table(printerName, jobLog):
  options = ConfigSectionMap(printerName) # get printer settings
  userLog = get_user_log(jobLog, printerName, options["lastDate"])
  userList = []
  for name in userLog:
    user = userLog[name]
    value = [name, user["paginas"], user["kosten"]]
    userList.append(value)
  return tabulate(userList, headers=["User", "Pages", "Price"])

def write_to_file(table):
  filename = "./logs/printLog " + time.strftime("%d-%m-%Y") + ".txt"
  if not os.path.exists(os.path.dirname(filename)):
    try:
        os.makedirs(os.path.dirname(filename))
    except OSError: # Guard against race condition
      if not os.path.isdir(filename):
          raise
  with open(filename, "w+") as text_file: # open file, and create if it does not exist
    text_file.write(table)

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