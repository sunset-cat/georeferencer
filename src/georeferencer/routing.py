"""Route physical entities using their extraction metadata."""
from .models import Category, VerificationClass

MINING_CLASSES = {VerificationClass.mineral_tenure, VerificationClass.drilling_site, VerificationClass.mineral_site}


def is_mining_asset(entity):
    return entity.category == Category.mining or bool(MINING_CLASSES.intersection(entity.verification_classes))


def uses_geocoder(entity):
    return not is_mining_asset(entity) and (
        entity.category == Category.geographic or VerificationClass.geographic_place in entity.verification_classes)
