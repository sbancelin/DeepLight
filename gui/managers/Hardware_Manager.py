from PySide6.QtCore import QObject, Slot

from .Positioner_Manager import MockPositionerManager, HardwarePositionerManager


class HardwareManager(QObject):
    """
    Façade / factory hardware.

    Rôle :
    - exposer les actions instrumentales communes (ex: shutter)
    - fournir les managers concrets selon le backend choisi
    """

    def __init__(self, backend_name: str = "mock", parent=None):
        super().__init__(parent)
        self.backend_name = (backend_name or "mock").lower()
        self._positioner_axes = ["x", "y", "z", "p"]

    def create_positioner_manager(self, parent=None):
        """
        Retourne le manager de positionnement adapté au backend courant.
        """
        if self.backend_name == "nidaq":
            return HardwarePositionerManager(self._positioner_axes, parent=parent)

        return MockPositionerManager(self._positioner_axes, parent=parent)

    @Slot(bool)
    def set_shutter(self, open_: bool):
        if self.backend_name == "nidaq":
            # TODO: brancher ici la vraie commande hardware shutter
            pass
        else:
            # mock / no-op
            pass