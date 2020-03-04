"""
Message log module.

Logs data being sent in case of in connection and sends it when connection is established.

Attributes:
    REMOVE_OLD: Removes the old data.
    REMOVE_RECENT: Doesnt add the most recent data.

"""
import os
import re
import sys
import json
import zipfile
import logging
import datetime
from json.decoder import JSONDecodeError


REMOVE_OLD = 1
REMOVE_RECENT = 2


class MessageLog:
    """
    Message logger.

    Saves data not being sent due to no connection.
    """

    def __init__(self, log_offline, log_location, log_data_limit, limit_action, lines_to_remove):
        """
        Initialize MessageLog class.

        Sets up message logging enviroment.

        Args:
            log_offline: boolean indicating if data should be logged
            log_location: location of the logs
            log_data_limit: limit of data to be saved in log (bytes)
            limit_action: action to take when limit is reached
            lines_to_remove: how many lines to remove from the file

        Raises:
            ServerConnectionException: "Unable to connect to the server.

        """
        self.wapp_log = logging.getLogger(__name__)
        self.wapp_log.addHandler(logging.NullHandler())

        self.log_offline = log_offline
        self.log_data_limit = log_data_limit
        self.limit_action = limit_action
        self.lines_to_remove = lines_to_remove

        self.set_location(log_location)

    def set_location(self, log_location):
        """
        Set log location.

        Sets log location and creates log folder if necassary.

        Args:
            log_location: location of the logs.

        """
        self.log_location = log_location
        if self.log_offline:
            os.makedirs(self.log_location, exist_ok=True)

    def get_file_path(self, file_name):
        """
        Gets path to the file.

        Concatenates location of the logs and file name.

        Args:
            file_name: name of the file.

        Returns:
            path to the file.
        """
        return self.log_location + "/" + file_name

    def get_log_name(self):
        """
        Gets name of the newest log.

        Creates and returns the name of the log.

        Returns:
            name of the latest log
        """
        now = datetime.datetime.now()
        return str(now.year) + "-" + str(now.month) + "-" + str(now.day) + ".txt"

    def get_logs(self):
        """
        Gets log files in the location.

        Gets all files from directory and return ones that follow log file format.

        Returns:
            list of log file names.
        """
        file_list = enumerate(os.listdir(self.log_location))
        return [file_name for id, file_name in file_list if
                re.search("[0-9][0-9][0-9][0-9]-((0|)[0-9]|1[0-2])-((|1|2)[0-9]|3[0-1])", file_name)]

    def compact_logs(self):
        """
        Compacts all logs to save space.

        Uses all logs received from "get_logs" method and compacts the ones that
        are of type text, after compacting the text file is deleted.
        """
        all_logs = self.get_logs()
        text_logs = [file_name for id, file_name in enumerate(all_logs) if re.search(".txt$", file_name)]
        for file_name in text_logs:
            file_path = self.get_file_path(file_name)
            zip_file = zipfile.ZipFile(file_path.replace(".txt", ".zip"), "w")
            zip_file.write(file_path, file_name)
            zip_file.close()
            os.remove(file_path)

    def get_oldest_log(self):
        """
        Gets the oldest log.

        Uses all logs received from "get_logs" method and sorts them, taking the first (oldest)
        later calls "get_text_log" to ensure file is of text type.

        Returns:
            name of the file.
        """
        all_logs = self.get_logs()
        all_logs.sort()
        file_name = all_logs[0]
        return self.get_text_log(file_name)

    def get_text_log(self, file_name):
        """
        Gets name of the text file.

        Checks if the file is compacted, if it is then it is unzipped and new name is returned.

        Args:
            file_name: name of the file.

        Returns:
            name of the file.
        """
        if re.search(".zip$", file_name):
            file_path = self.get_file_path(file_name)
            with zipfile.ZipFile(file_path, "r") as zip_file:
                zip_file.extractall(self.log_location)
            os.remove(file_path)
            file_name = file_name.replace(".zip", ".txt")
        return file_name

    def remove_first_lines(self, file_name):
        """
        Removes first lines from file.

        Removes specific number of lines from the file, if the number
        of lines to remove exceeds lines in file, the file is deleted.

        Args:
            file_name: name of the file.

        """
        file_path = self.get_file_path(file_name)
        with open(file_path, "r") as file:
            lines = file.readlines()
        if self.lines_to_remove < len(lines):
            with open(file_path, "w") as file:
                file.writelines(lines[self.lines_to_remove:])
        else:
            os.remove(file_path)
        self.wapp_log.debug("Removed old data")

    def add_message(self, data):
        """
        Add message to log.

        Adds message to log if logging is enabled otherwise writes error.

        Args:
            data: JSON communication message data.

        """
        if self.log_offline:
            try:
                string_data = json.dumps(data)
                if self.log_data_limit >= self.get_size(string_data):
                    file_path = self.get_file_path(self.get_log_name())
                    if not os.path.isfile(file_path):
                        # compact data if log for this day doesnt exist
                        self.compact_logs()
                    file = open(file_path, "a")
                    file.write(string_data + " \n")
                    file.close()
                    self.wapp_log.debug("Raw log Json: {}".format(string_data))
                else:
                    self.wapp_log.debug("Log limit exeeded.")
                    if self.limit_action == REMOVE_OLD:
                        file_name = self.get_oldest_log()
                        self.remove_first_lines(file_name)
                        self.add_message(data)
                    elif self.limit_action == REMOVE_RECENT:
                        self.wapp_log.debug("Not adding data")
            except FileNotFoundError:
                msg = "No log file could be created in: {}".format(self.log_location)
                self.wapp_log.error(msg)
        else:
            self.wapp_log.error("Sending while not connected")

    def get_size(self, data):
        """
        Gets size of log folder.

        Method loops through all file and gets their total size in the folder.

        Args:
            data: JSON communication message data.

        Returns:
            Total size of the folder.
        """
        total_size = 0
        for dirpath, dirnames, file_names in os.walk(self.log_location):
            for file_name in file_names:
                file_path = os.path.join(dirpath, file_name)
                # skip if it is link
                if not os.path.islink(file_path):
                    total_size += os.path.getsize(file_path)

        total_size += sys.getsizeof(data)
        return total_size

    def send_log(self, conn):
        """
        Sends log data.

        If logging is enabled reads all saved messages from log and sends them, later emptying log.

        Args:
            conn: reference to ClientSocket object.

        """
        if self.log_offline:
            try:
                log_list = self.get_logs()
                self.wapp_log.debug("Found log files: " + str(log_list))

                for file_name in log_list:
                    file_name = self.get_text_log(file_name)
                    file_path = self.get_file_path(file_name)
                    file = open(file_path, "r")
                    for line in file.readlines():
                        try:
                            data = json.loads(line)
                            for data_element in data:
                                conn.create_bulk(data_element)
                        except JSONDecodeError:
                            error = "Json decoding error while reading : {}".format(line)
                            self.wapp_log.error(error)
                    file.close()
                    self.wapp_log.debug("Data sent from file: " + file_path)
                    os.remove(file_path)
            except FileNotFoundError:
                error = "Log directory could not be found: {}".format(self.log_location)
                self.wapp_log.error(error)