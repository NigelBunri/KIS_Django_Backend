"""
Server-side notification string localization.

Usage:
    from apps.accounts.notification_i18n import get_notification_string, get_user_language

    lang = get_user_language(recipient_user)
    body = get_notification_string(lang, 'new_message')
"""

NOTIFICATION_STRINGS = {
    'en': {
        'new_message': 'New message',
        'new_status': 'posted a status',
        'booking_confirmed': 'Booking confirmed',
        'booking_cancelled': 'Booking cancelled',
        'booking_reminder': 'Upcoming appointment',
        'booking_updated': 'Booking updated',
    },
    'fr': {
        'new_message': 'Nouveau message',
        'new_status': 'a publié un statut',
        'booking_confirmed': 'Réservation confirmée',
        'booking_cancelled': 'Réservation annulée',
        'booking_reminder': 'Rendez-vous à venir',
        'booking_updated': 'Réservation mise à jour',
    },
    'es': {
        'new_message': 'Nuevo mensaje',
        'new_status': 'publicó un estado',
        'booking_confirmed': 'Reserva confirmada',
        'booking_cancelled': 'Reserva cancelada',
        'booking_reminder': 'Cita próxima',
        'booking_updated': 'Reserva actualizada',
    },
    'pt': {
        'new_message': 'Nova mensagem',
        'new_status': 'publicou um status',
        'booking_confirmed': 'Reserva confirmada',
        'booking_cancelled': 'Reserva cancelada',
        'booking_reminder': 'Consulta próxima',
        'booking_updated': 'Reserva atualizada',
    },
    'sw': {
        'new_message': 'Ujumbe mpya',
        'new_status': 'alichapisha hali',
        'booking_confirmed': 'Ufungaji umethibitishwa',
        'booking_cancelled': 'Ufungaji umesitishwa',
        'booking_reminder': 'Miadi inakuja',
        'booking_updated': 'Ufungaji umesasishwa',
    },
}


def get_notification_string(user_language: str, key: str) -> str:
    """Return the localised notification string for *key* in *user_language*.

    Falls back to English if the language or key is not found.

    Args:
        user_language: BCP-47 tag such as ``'fr'``, ``'fr-FR'``, or ``'en-US'``.
        key: One of the keys defined in :data:`NOTIFICATION_STRINGS`.
    """
    lang = user_language.split('-')[0].lower() if user_language else 'en'
    strings = NOTIFICATION_STRINGS.get(lang, NOTIFICATION_STRINGS['en'])
    return strings.get(key, NOTIFICATION_STRINGS['en'].get(key, key))


def get_user_language(user) -> str:
    """Return the preferred language tag for *user*, defaulting to ``'en'``.

    Reads ``user.profile_preferences.language_preference`` if it exists.
    Also checks the ``locale`` field on the User model as a fallback.
    Never raises — always returns a non-empty string.
    """
    try:
        lang = getattr(user.profile_preferences, 'language_preference', None)
        if lang:
            return lang
    except Exception:
        pass
    try:
        locale = getattr(user, 'locale', None)
        if locale:
            return locale
    except Exception:
        pass
    return 'en'
