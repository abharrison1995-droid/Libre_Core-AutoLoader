from .models import get_model, FirmwareBase, PayloadType

def is_firmware_supported(model_id: str, firmware: FirmwareBase) -> bool:
    """Check if a firmware base is supported on a given model.
    
    Args:
        model_id: The ThinkPad model ID.
        firmware: The firmware base to check.
        
    Returns:
        True if supported, False otherwise.
    """
    try:
        model = get_model(model_id)
    except KeyError:
        return False
        
    if firmware == FirmwareBase.COREBOOT:
        return model.coreboot_support
    elif firmware == FirmwareBase.LIBREBOOT:
        return model.libreboot_support
    elif firmware == FirmwareBase.CANOEBOOT:
        return model.canoeboot_support
        
    return False

def is_payload_supported(model_id: str, payload: PayloadType) -> bool:
    """Check if a payload is supported on a given model.
    
    Args:
        model_id: The ThinkPad model ID.
        payload: The payload to check.
        
    Returns:
        True if supported, False otherwise.
    """
    try:
        model = get_model(model_id)
        return payload in model.supported_payloads
    except KeyError:
        return False

def validate_configuration(model_id: str, firmware: FirmwareBase, payload: PayloadType) -> list[str]:
    """Validate a proposed configuration.
    
    Args:
        model_id: The ThinkPad model ID.
        firmware: The firmware base.
        payload: The payload.
        
    Returns:
        A list of error strings. Empty if configuration is valid.
    """
    errors = []
    
    if not is_firmware_supported(model_id, firmware):
        errors.append(f"Firmware {firmware.value} is not supported on model {model_id}.")
        
    if not is_payload_supported(model_id, payload):
        errors.append(f"Payload {payload.value} is not supported on model {model_id}.")
        
    return errors
