from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecordingIdentity:
    recording_id: str
    device_id: str

    def __post_init__(self) -> None:
        for label, value in (('recording ID', self.recording_id), ('device ID', self.device_id)):
            if not isinstance(value, str) or not value:
                raise ValueError(f'{label} must be a nonempty string')
