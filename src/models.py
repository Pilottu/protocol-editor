from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Patsient:
    PatsientID: Optional[int] = None
    FIO: str = ""
    Pol: str = ""
    Karta: str = ""
    PatsientGroupID: Optional[int] = None
    OrganizatsiaID: Optional[int] = None


@dataclass
class Protocol:
    ProtocolID: Optional[int] = None
    PatsientID: Optional[int] = None
    Vozrast: Optional[int] = None
    OrganizatsiaID: Optional[int] = None
    Nomer: str = ""
    ProtocolDate: Optional[datetime] = None
    Anestezia: str = ""
    ProtocolText: str = ""
    Diagnos: str = ""
    OtdelenieID: Optional[int] = None
    Adres: str = ""
    Istor: str = ""
    ApparatID: Optional[int] = None
    Otdelenie: str = ""
    Anamnez: str = ""
    Biopsia: str = ""
    IssledovanieID: Optional[int] = None
    Year: Optional[int] = None
    Tsitologia: str = ""
    Gistologia: str = ""
    Lecheb: str = ""
    Sanats: str = ""
    Intybastia: str = ""
    PHMetr: str = ""
    Smiv: str = ""
    State: