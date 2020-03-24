"""
The network module.

Stores attributes for the network instance.
"""
import logging
from ..connection import message_data
from ..errors import wappsto_errors


class Network:
    """
    Network instance class.

    Stores attributes for the network instance.
    """

    def __init__(self, uuid, version, name, devices, data_manager):
        """
        Initialize the Network class.

        Initializes an object of network class by passing required parameters.

        Args:
            uuid: Unique identifier of a network
            version: Version of a network
            name: Name of a network
            devices: list of devices in network
            data_manager: Instance of DataManager

        """
        self.wapp_log = logging.getLogger(__name__)
        self.wapp_log.addHandler(logging.NullHandler())
        self.uuid = uuid
        self.version = version
        self.name = name
        self.devices = devices
        self.data_manager = data_manager
        self.rpc = None
        self.conn = None
        self.callback = self.__callback_not_set
        msg = "Network {} Debug \n{}".format(name, str(self.__dict__))
        self.wapp_log.debug(msg)

    def set_callback(self, callback):
        """
        Set the callback.

        Sets the callback attribute. It will be called by the __send_logic
        method.

        Args:
            callback: Callback reference.

        Raises:
            CallbackNotCallableException: Custom exception to signify invalid
            callback.

        """
        try:
            if not callable(callback):
                msg = "Callback method should be a method"
                raise wappsto_errors.CallbackNotCallableException(msg)
            self.callback = callback
            self.wapp_log.debug("Callback {} has been set.".format(callback))
            return True
        except wappsto_errors.CallbackNotCallableException as e:
            self.wapp_log.error("Error setting callback: {}".format(e))
            raise

    def handle_delete(self):
        """
        Handle delete.

        Calls the __call_callback method with initial input of "remove".

        Returns:
            result of __call_callback method.

        """
        self.__call_callback('remove')

    def delete(self):
        """
        Delete this object.

        Sends delete request for this object and removes its reference
        from parent.

        """
        message = message_data.MessageData(
            message_data.SEND_DELETE,
            network_id=self.uuid,
        )
        self.conn.sending_queue.put(message)
        self.data_manager.network = None
        self.wapp_log.info("Network removed")

    def __callback_not_set(self, network, event):
        """
        Message about no callback being set.

        Temporary method to signify that there is no callback set for the
        class.

        Returns:
            "Callback not set" message.

        """
        msg = "Callback for network '{}' is not set".format(self.name)
        return self.wapp_log.debug(msg)

    def __call_callback(self, event):
        if self.callback is not None:
            self.callback(self, event)