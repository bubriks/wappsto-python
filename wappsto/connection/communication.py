"""
The server communication module.

Stores the server communication functionality for Wappsto as well as the
sending and receiving threads.
"""

import os
import sys
import socket
import threading
import random
import time
import json
import queue
import ssl
# REPLACED request WITH NATIVE MODULE
import urllib.request as request
import logging
from . import message_data
from .. import status
from ..errors import wappsto_errors
from json.decoder import JSONDecodeError

RECEIVE_SIZE = 1024
MESSAGE_SIZE_BYTES = 1000000
MAX_BULK_SIZE = 10
t_url = 'https://tracer.iot.seluxit.com/trace?id={}&parent={}&name={}&status={}'  # noqa: E501


class ClientSocket:
    """
    The ClientSocket class that handles sending and receiving.

    Stores the sending and receiving threads, certificates, connection
    information as well as the related methods for handling communication
    between the client and the server.
    """

    def __init__(self, rpc, data_manager, address, port, path_to_calling_file,
                 wappsto_status, automatic_trace, event_storage):
        """
        Create a client socket.

        Creates a socket instance for the given address and port. Handles
        transfer of data from the instance attributes and methods to the
        specified server. Connection to the server is based on the specified
        address and port.

        Args:
            rpc: Sending/receiving queue processing instance.
            data_manager: data_manager of DataManager.
            address: Server address.
            port: Server port.
            path_to_calling_file: path to OS directory of calling file.
            wappsto_status: status object.
            automatic_trace: indicates if all messages automaticaly send trace.
            handler: instance of handlers.
            event_storage: instance of event log.

        """
        self.wapp_log = logging.getLogger(__name__)
        self.wapp_log.addHandler(logging.NullHandler())
        self.data_manager = data_manager
        self.path_to_calling_file = path_to_calling_file
        self.ssl_server_cert = os.path.join(path_to_calling_file,
                                            "certificates/ca.crt")
        self.ssl_client_cert = os.path.join(path_to_calling_file,
                                            "certificates/client.crt")
        self.ssl_key = os.path.join(path_to_calling_file,
                                    "certificates/client.key")
        self.address = address
        self.port = port
        self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED
        self.ssl_context.load_cert_chain(self.ssl_client_cert, self.ssl_key)
        self.ssl_context.load_verify_locations(self.ssl_server_cert)
        self.wappsto_status = wappsto_status
        self.automatic_trace = automatic_trace
        self.receiving_thread = threading.Thread(target=self.receive_thread)
        self.receiving_thread.setDaemon(True)
        self.connected = False
        self.sending_queue = queue.Queue(maxsize=0)
        self.sending_thread = threading.Thread(target=self.send_thread)
        self.sending_thread.setDaemon(True)
        self.rpc = rpc
        self.event_storage = event_storage
        self.packet_awaiting_confirm = {}
        self.add_trace_to_report_list = {}
        self.bulk_send_list = []
        self.lock_await = threading.Lock()
        self.set_sockets()

        self.data_manager.network.rpc = self.rpc
        self.data_manager.network.conn = self

    def send_state(self, state, data_value=None):
        """
        Send control or report to a server.

        Sends a control or report message with a new value to the server.

        Args:
            state: Reference to an instance of a State class.
            data_value: A new incoming value.

        Raises:
            Exception: If one occurs while sending control message.

        """
        trace_id = self.create_trace(state.uuid)

        json_data = self.rpc.get_rpc_state(
            str(data_value),
            state.parent.parent.parent.uuid,
            state.parent.parent.uuid,
            state.parent.uuid,
            state.uuid,
            state.state_type,
            state_obj=state,
            trace_id=trace_id
        )
        self.create_bulk(json_data)
        return True

    def set_sockets(self):
        """
        Create socket to communicate with server.

        Creates a socket instance and sets the options for communication.
        Passes the socket to the ssl_wrap method
        """
        self.my_raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.my_raw_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_KEEPALIVE,
            1
        )
        if (hasattr(socket, "TCP_KEEPIDLE")
                and hasattr(socket, "TCP_KEEPINTVL")
                and hasattr(socket, "TCP_KEEPCNT")):
            # After 5 idle minutes, start sending keepalives every 1 minutes.
            # Drop connection after 2 failed keepalives
            self.my_raw_socket.setsockopt(
                socket.SOL_TCP,
                socket.TCP_KEEPIDLE,
                5 * 60
            )
            self.my_raw_socket.setsockopt(
                socket.IPPROTO_TCP,
                socket.TCP_KEEPIDLE,
                5 * 60
            )
            self.my_raw_socket.setsockopt(
                socket.IPPROTO_TCP,
                socket.TCP_KEEPINTVL,
                60
            )
            self.my_raw_socket.setsockopt(
                socket.IPPROTO_TCP,
                socket.TCP_KEEPCNT,
                2
            )
            # self.my_raw_socket.setsockopt(
            #     socket.IPPROTO_TCP,
            #     socket.TCP_USER_TIMEOUT,
            #     30000
            # )
        self.my_socket = self.ssl_wrap()

    def ssl_wrap(self):
        """
        Wrap socket.

        Wraps the socket using the SSL protocol as configured in the SSL
        context, with hostname verification enabled.

        Returns:
        An SSL wrapped socket.

        """
        return self.ssl_context.wrap_socket(
            self.my_raw_socket,
            server_hostname=self.address
        )

    def connect(self):
        """
        Connect to the server.

        Attempts a connection to the server on the provided addres and port.

        Returns:
            A connection flag to denote if the connection was successful or
            not.

        """
        self.connected = False
        try:
            self.my_socket.settimeout(10)
            self.my_socket.connect((self.address, self.port))
            self.connected = True
            self.my_socket.settimeout(None)
            self.wappsto_status.set_status(status.CONNECTED)
            self.send_logged_data()
            return True

        except Exception as e:
            self.wapp_log.error("Failed to connect: {}".format(e))
            return False

    def send_logged_data(self):
        """
        Sends logged data.

        Makes a thread that sends all of the logged data.
        """
        processThread = threading.Thread(target=self.event_storage.send_log, args=(self,))
        processThread.start()

    def initialize_all(self):
        """
        Call initialize_all method in initialize_code module.

        Initializes the object instances on the sending/receiving queue.
        """
        for device in self.data_manager.network.devices:
            for value in device.values:
                state = value.get_control_state()
                if state is not None:
                    self.get_control(state)

        trace_id = self.create_trace(self.data_manager.network.uuid)
        message = self.rpc.get_rpc_whole_json(self.data_manager.get_encoded_network(), trace_id)
        self.create_bulk(message)

        msg = "The whole network {} added to Sending queue {}.".format(
            self.data_manager.network.name,
            self.rpc
        )
        self.wapp_log.debug(msg)

        self.confirm_initialize_all()

    def add_id_to_confirm_list(self, data):
        """
        Add the message ID to the confirm list.

        Adds the ID of the decoded JSON message to the list of confirmed
        packets. Uses locks to ensure atomicity.

        Args:
            data: JSON communication message data.

        """
        self.lock_await.acquire()
        self.packet_awaiting_confirm[data.get('id')] = data
        self.lock_await.release()

    def remove_id_from_confirm_list(self, _id):
        """
        Remove the ID from the confirm list.

        Removes the ID of the decoded JSON message from the list of confirmed
        packets. Uses locks to ensure atomicity.

        Args:
            _id: ID to remove from the confirm list.

        """
        self.lock_await.acquire()
        if _id in self.packet_awaiting_confirm:
            del self.packet_awaiting_confirm[_id]
        self.lock_await.release()

    def incoming_control(self, data):
        """
        Incoming data handler.

        Gets the data from incoming control messages and performs the expected changes.

        Args:
            data: JSON communication message data.

        Returns:
            Results of the incoming control handling.

        """
        return_id = data.get('id')
        try:
            uuid = data.get('params').get('data').get('meta').get('id')
            meta_type = data.get('params').get('data').get('meta').get('type')
            self.wapp_log.debug("Put request from id: " + uuid)
        except AttributeError:
            error_str = 'Error received incorrect format in put: {}'.format(str(data))
            self.wapp_log.error(error_str, exc_info=True)
            return

        try:
            trace_id = data.get('params').get('meta').get('trace')
            if trace_id:
                self.wapp_log.debug("Found trace id: " + trace_id)
        except AttributeError:
            trace_id = None

        obj = self.data_manager.get_by_id(uuid)
        if obj is None:
            self.send_error('Non-existing uuid provided', return_id)
            return

        try:
            if meta_type == "value":
                period = data.get('params').get('data').get('period')
                obj.set_period(period)
                delta = data.get('params').get('data').get('delta')
                obj.set_delta(delta)
                self.sending_queue_add_trace(
                    obj.parent.uuid,
                    trace_id,
                    None,
                    control_value_id=self.__get_random_id()
                )
                self.send_success_reply(return_id)
            elif meta_type == "state":
                local_data = data.get('params').get('data').get('data')
                if obj.state_type == "Control":
                    obj.parent.handle_control(data_value=local_data)
                    self.sending_queue_add_trace(
                        obj.parent.uuid,
                        trace_id,
                        local_data,
                        control_value_id=self.__get_random_id()
                    )
                    self.send_success_reply(return_id)
                else:
                    self.send_error('Element is not control state', return_id)
        except AttributeError:
            self.send_error('Attribute error encountered', return_id)

    def __get_random_id(self):
        network_n = self.data_manager.network.name
        random_int = random.randint(1, 25000)
        return "{}{}".format(network_n, random_int)

    def sending_queue_add_trace(self, parent, trace_id, data, control_value_id=None):
        """
        Add a trace to the sending queue.

        Adds a trace URL to the sending queue for debugging purposes.

        Args:
            parent: Owner of the trace URL.
            trace_id: ID of the trace message.
            data: Trace message data.
            control_value_id: ID of the control state of the value
                (default: {None})

        """
        if trace_id:
            trace = message_data.MessageData(
                message_data.SEND_TRACE,
                parent=parent,
                trace_id=trace_id,
                data=data,
                text="ok",
                control_value_id=control_value_id)
            self.sending_queue.put(trace)

    def send_success_reply(self, return_id):
        """
        Handle successful replies on the receive thread.

        Send back a success message based on the message return ID.

        Args:
            return_id: ID of the success message.

        """
        success_reply = message_data.MessageData(
            message_data.SEND_SUCCESS,
            rpc_id=return_id
        )
        self.sending_queue.put(success_reply)

    def send_error(self, error_str, return_id):
        """
        Send error message.

        Sends back an error message based on the message return ID.

        Args:
            error_str: Error message contents.
            return_id: ID of the error message.

        """
        error_reply = message_data.MessageData(
            message_data.SEND_FAILED,
            rpc_id=return_id,
            text=error_str
        )
        self.sending_queue.put(error_reply)

    def incoming_report_request(self, data):
        """
        Incoming data handler.

        Gets the data from incoming report messages and performs the expected changes.

        Args:
            data: JSON communication message data.

        Returns:
            Results of the incoming report handling.

        """
        return_id = data.get('id')
        try:
            uuid = data.get('params').get('data').get('meta').get('id')
            self.wapp_log.debug("Get request from id: " + uuid)
        except AttributeError:
            error_str = 'Error received incorrect format in get: {}'.format(str(data))
            self.wapp_log.error(error_str, exc_info=True)
            return

        try:
            trace_id = data.get('params').get('meta').get('trace')
            if trace_id:
                self.wapp_log.debug("Found trace id: " + trace_id)
        except AttributeError:
            trace_id = None

        obj = self.data_manager.get_by_id(uuid)
        if obj is None:
            self.send_error('Non-existing uuid provided', return_id)
            return

        try:
            if obj.state_type == "Report":
                self.sending_queue_add_trace(
                    obj.parent.uuid,
                    trace_id,
                    obj.data,
                    control_value_id=self.__get_random_id()
                )
                obj.parent.handle_refresh()
                self.send_success_reply(return_id)
            else:
                self.send_error('Element is not control state', return_id)
        except AttributeError:
            self.send_error('Attribute error encountered', return_id)

    def incoming_delete_request(self, data):
        """
        Incoming delete handler.

        Gets the data from incoming delete messages and performs the expected changes.

        Args:
            data: JSON communication message data.

        Returns:
            Results of the incoming report handling.

        """
        return_id = data.get('id')
        try:
            uuid = data.get('params').get('data').get('meta').get('id')
            self.wapp_log.debug("Delete request from id: " + uuid)
        except AttributeError:
            error_str = 'Error received incorrect format in delete: {}'.format(str(data))
            self.wapp_log.error(error_str, exc_info=True)
            return

        try:
            trace_id = data.get('params').get('meta').get('trace')
            if trace_id:
                self.wapp_log.debug("Found trace id: " + trace_id)
        except AttributeError:
            trace_id = None

        obj = self.data_manager.get_by_id(uuid)
        if obj is None:
            self.send_error('Non-existing uuid provided', return_id)
            return

        try:
            self.sending_queue_add_trace(
                obj.uuid,
                trace_id,
                None,
                control_value_id=self.__get_random_id()
            )
            obj.handle_delete()
            self.send_success_reply(return_id)
        except AttributeError:
            self.send_error('Attribute error encountered', return_id)

    def receive_thread(self):
        """
        Create the receive thread.

        Starts a while True loop checking if something is received.
        """
        self.wapp_log.debug("ReceiveThread Started!")
        while True:
            self.receive_message()

    def reconnect(self, retry_limit=None, send_reconnect=True):
        """
        Attempt to reconnect.

        Reconnection attemps in the instance of a connection being interrupted.
        """
        self.wappsto_status.set_status(status.RECONNECTING)
        self.connected = False
        attempt = 0
        while not self.connected and (retry_limit is None
                                      or retry_limit > attempt):
            attempt += 1
            self.wapp_log.info("Trying to reconnect in 5 seconds")
            time.sleep(5)
            self.close()
            self.set_sockets()
            self.connect()

        if self.connected is True:
            self.wapp_log.info("Reconnected with " + str(attempt) + " attempts")
            if send_reconnect:
                reconnect = message_data.MessageData(message_data.SEND_RECONNECT)
                self.sending_queue.put(reconnect)
        else:
            msg = ("Unable to connect to the server[IP: {}, Port: {}]"
                   .format(self.address, self.port)
                   )
            raise wappsto_errors.ServerConnectionException(msg)

    def create_bulk(self, data):
        """
        Creates bulk message.

        Accomulates all messages in one and once sending_queue is empty or
        bulk limit is reached it is sent.

        Args:
            data: JSON communication message data.

        """
        try:
            if data is not None:
                self.bulk_send_list.append(data)
            if ((self.sending_queue.qsize() == 0 and len(self.packet_awaiting_confirm) == 0)
                    or len(self.bulk_send_list) >= MAX_BULK_SIZE):
                self.send_data(self.bulk_send_list)
                self.bulk_send_list.clear()
        except OSError as e:  # pragma: no cover
            self.connected = False
            msg = "Error sending message: {}".format(e)
            self.wapp_log.error(msg, exc_info=True)

    def send_data(self, data):
        """
        Send JSON data.

        Sends the encoded JSON message through the socket.

        Args:
            data: JSON communication message data.

        """
        for data_element in data:
            self.get_object_without_none_values(data_element)
            if len(data_element) == 0:
                data.remove(data_element)

        if self.connected:
            for data_element in data:
                if data_element.get("method", "") in ["PUT", "POST", "DELETE"]:
                    self.add_id_to_confirm_list(data_element)
            if len(data) > 0:
                data = json.dumps(data)
                data = data.encode('utf-8')
                self.wapp_log.debug('Raw Send Json: {}'.format(data))
                self.my_socket.send(data)
        else:
            self.event_storage.add_message(data)

    def get_object_without_none_values(self, encoded_object):
        """
        Get object without None values.

        Gets objects and removes any keys where value is None.

        Args:
            encoded_object: dictionary object.

        """
        for key, val in list(encoded_object.items()):
            if val is None:
                del encoded_object[key]
            elif isinstance(val, dict):
                self.get_object_without_none_values(val)
                if len(val) == 0:
                    del encoded_object[key]
            elif isinstance(val, list):
                for val_element in val:
                    self.get_object_without_none_values(val_element)
                    if len(val_element) == 0:
                        val.remove(val_element)
                if len(val) == 0:
                    del encoded_object[key]

    def send_thread(self):
        """
        Create a send thread.

        Retrieves packages from the sending queue to
        send data.
        """
        self.wapp_log.debug("SendingThread Started!")

        while True:
            package = self.sending_queue.get()
            if package.msg_id == message_data.SEND_SUCCESS:
                self.send_success(package)

            elif package.msg_id == message_data.SEND_REPORT:
                self.send_report(package)

            elif package.msg_id == message_data.SEND_FAILED:
                self.send_failed(package)

            elif package.msg_id == message_data.SEND_RECONNECT:
                self.send_reconnect(package)

            elif package.msg_id == message_data.SEND_CONTROL:
                self.send_control(package)

            elif package.msg_id == message_data.SEND_TRACE:
                self.send_trace(package)

            elif package.msg_id == message_data.SEND_DELETE:
                self.send_delete(package)

            else:
                self.wapp_log.warning("Unhandled send")

            self.sending_queue.task_done()

    def send_delete(self, package):
        """
        Send data delete request.

        Sends the data to be deleted.

        Args:
            package: Sending queue item.

        """
        self.wapp_log.info("Sending delete message")
        package.trace_id = self.create_trace(
            package.network_id, package.trace_id)

        local_data = self.rpc.get_rpc_delete(
            package.network_id,
            package.device_id,
            package.value_id,
            package.state_id,
            package.trace_id
        )
        self.create_bulk(local_data)

    def get_control(self, state):
        """
        Send get control state data.

        Sends requests for the data of control state.

        Args:
            state: State object referece.

        """
        self.wapp_log.info("Getting control value")
        local_data = self.rpc.get_rpc_state(
            None,
            state.parent.parent.parent.uuid,
            state.parent.parent.uuid,
            state.parent.uuid,
            state.uuid,
            state.state_type,
            get=True
        )
        self.create_bulk(local_data)

    def send_trace(self, package):
        """
        Send data trace.

        Provides a trace URL for debugging purposes.

        Args:
            package: Sending queue item.

        """
        if package.control_value_id:
            control_value_id = package.control_value_id
            self.add_trace_to_report_list[control_value_id] = package.trace_id

        attempt = str(t_url).format(
            package.trace_id,
            package.parent,
            package.data,
            package.text
        )

        context = ssl._create_unverified_context()
        trace_req = request.urlopen(attempt, context=context)
        msg = "Sending tracer https message {} response {}".format(attempt, trace_req.getcode())
        self.wapp_log.debug(msg)

    def create_trace(self, parent, trace_id=None):
        """
        Creates trace.

        Creates trace if necessary, by using generated data and existing
        information.

        Args:
            parent: owner of trace.
            trace_id: existing id used for tracing.

        Returns:
            trace id.

        """
        if self.automatic_trace and trace_id is None:
            random_int = random.randint(1, 25000)
            control_value_id = "{}{}".format(self.data_manager.network.name,
                                             random_int)

            trace_id = random_int

            trace = message_data.MessageData(
                message_data.SEND_TRACE,
                parent=parent,
                trace_id=trace_id,
                data=None,
                text="ok",
                control_value_id=control_value_id)
            self.send_trace(trace)
        return trace_id

    def send_control(self, package):
        """
        Send data handler.

        Sends control message for the data.

        Args:
            package: Sending queue item.

        """
        self.wapp_log.info("Sending control message")
        package.trace_id = self.create_trace(
            package.network_id, package.trace_id)

        local_data = self.rpc.get_rpc_state(
            package.data,
            package.network_id,
            package.device_id,
            package.value_id,
            package.state_id,
            'Control',
            trace_id=package.trace_id
        )
        self.create_bulk(local_data)

    def receive_data(self):
        """
        Socket receive method.

        Method that handles receiving data from a socket. Capable of handling
        data chunks.

        Returns:
            The decoded message from the socket.

        """
        total_decoded = ''
        decoded = None
        while True:
            if self.connected:
                data = self.my_socket.recv(RECEIVE_SIZE)
                if data == b'':
                    self.reconnect()
                    return None
                try:
                    decoded_data = data.decode('utf-8')
                except AttributeError:
                    continue
                total_decoded += decoded_data
                if sys.getsizeof(total_decoded) > MESSAGE_SIZE_BYTES:
                    error = "Received message exeeds size limit."
                    self.wapp_log.error(error)
                    return None
                try:
                    decoded = json.loads(total_decoded)
                except JSONDecodeError:
                    if len(decoded_data) < RECEIVE_SIZE:
                        error = "Json decoding error: {}".format(total_decoded)
                        self.wapp_log.error(error)
                        return None
                else:
                    break
            else:
                break
        return decoded

    def send_reconnect(self, package):
        """
        Send a reconnect attempt.

        Sends a request to attempt to reconnect to the server.
        """
        self.wapp_log.info("Sending reconnect data")
        try:
            package.trace_id = self.create_trace(
                package.network_id, package.trace_id)

            rpc_network = self.rpc.get_rpc_network(
                self.data_manager.network.uuid,
                self.data_manager.network.name,
                put=False,
                trace_id=package.trace_id
            )
            self.create_bulk(rpc_network)
            for element in self.packet_awaiting_confirm:
                self.create_bulk(self.packet_awaiting_confirm[element])
        except OSError as e:
            self.connected = False
            msg = "Error sending reconnect: {}".format(e)
            self.wapp_log.error(msg, exc_info=True)

    def send_failed(self, package):
        """
        Send a fail message.

        Sends a message to notify about a sending failure.

        Args:
            package: Sending queue item.

        """
        self.wapp_log.info("Sending failed")

        rpc_fail_response = self.rpc.get_rpc_fail_response(
            package.rpc_id,
            package.text
        )
        self.create_bulk(rpc_fail_response)

    def send_report(self, package):
        """
        Send a report.

        Sends a report message from the package.

        Args:
            package: A sending queue item.

        """
        if not package.trace_id:
            if package.value_id in self.add_trace_to_report_list.keys():
                package.trace_id = (
                    self.add_trace_to_report_list.pop(package.value_id)
                )

        package.trace_id = self.create_trace(
            package.network_id, package.trace_id)

        local_data = self.rpc.get_rpc_state(
            package.data,
            package.network_id,
            package.device_id,
            package.value_id,
            package.state_id,
            'Report',
            trace_id=package.trace_id
        )
        self.create_bulk(local_data)
        data_decoded = local_data.get('params').get('data').get('data')
        self.wapp_log.info('Sending report value: {}'.format(data_decoded))

    def send_success(self, package):
        """
        Send a success message.

        Sends a message to notify of a successful.

        Args:
            package: A sending queue item.

        """
        rpc_success_response = self.rpc.get_rpc_success_response(
            package.rpc_id
        )
        self.create_bulk(rpc_success_response)

    def close(self):
        """
        Close the connection.

        Closes the socket object connection.
        """
        self.wapp_log.info("Closing connection...")

        for device in self.data_manager.network.devices:
            for value in device.values:
                if value.timer.is_alive():
                    msg = "Value: {} is no longer periodically sending updates.".format(value.uuid)
                    self.wapp_log.debug(msg)
                value.timer.cancel()

        self.connected = False
        if self.my_socket:
            self.my_socket.close()
            self.my_socket = None
        if self.my_raw_socket:
            self.my_raw_socket.close()
            self.my_raw_socket = None

    def confirm_initialize_all(self):
        """
        Confirms that all responses are received.

        Goes through the list saving expected responses and checks if they are
        received.
        """
        while len(self.packet_awaiting_confirm) > 0:
            self.receive_message()

    def receive_message(self):
        """
        Receives message.

        Receives message and passes it to receive method, and catches
        encountered exceptions.
        """
        try:
            decoded = self.receive_data()

            # if the received string is list
            if isinstance(decoded, list):
                for decoded_data in decoded:
                    self.receive(decoded_data)
            else:
                self.receive(decoded)

        except ConnectionResetError as e:  # pragma: no cover
            msg = "Received Reset: {}".format(e)
            self.wapp_log.error(msg, exc_info=True)
            self.reconnect()

        except OSError as oe:  # pragma: no cover
            msg = "Received OS Error: {}".format(oe)
            self.wapp_log.error(msg, exc_info=True)
            self.reconnect()

    def receive(self, decoded):
        """
        Performs acction on received message.

        Based on the type of message, directs the decoded data to the
        appropriate methods.

        Args:
            decoded: the received message

        """
        if decoded:
            decoded_id = decoded.get('id')
            try:
                self.wapp_log.debug('Raw received Json: {}'
                                    .format(decoded))
                if decoded.get('method', False) == 'PUT':
                    self.incoming_control(decoded)

                elif decoded.get('method', False) == 'GET':
                    self.incoming_report_request(decoded)

                elif decoded.get('method', False) == 'DELETE':
                    self.incoming_delete_request(decoded)

                elif decoded.get('error', False):
                    decoded_error = decoded.get('error')
                    msg = "Error: {}".format(decoded_error.get('message'))
                    self.wapp_log.error(msg)
                    self.remove_id_from_confirm_list(decoded_id)

                elif decoded.get('result', False):
                    result_value = decoded['result'].get('value', False)
                    if result_value:
                        uuid = result_value['meta']['id']
                        data = result_value['data']
                        object = self.data_manager.get_by_id(uuid)
                        if object is not None and object.parent.control_state == object:
                            object.parent.handle_control(data_value=data)
                    self.remove_id_from_confirm_list(decoded_id)

                else:
                    error_str = "Unknown method"
                    self.wapp_log.warning(error_str)
                    self.send_error(error_str, decoded_id)
            except ValueError:
                error_str = 'Value error'
                self.wapp_log.error("{} [{}]: {}".format(error_str, decoded_id, decoded))
                self.send_error(error_str, decoded_id)
