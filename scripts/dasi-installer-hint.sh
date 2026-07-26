# /etc/profile.d/dasi-installer-hint.sh — shown at login on dasi USB
# installation media only (gated on the media flag, which dasi-install
# does not copy to the installed system, so this stays silent there).
if [ -f /etc/dasi-installer-media ]; then
    echo
    echo "  ******************************************************************"
    echo "  *  This is dasi2 USB INSTALLATION MEDIA — the system is running  *"
    echo "  *  from the USB stick.  To install onto the internal disk, run:  *"
    echo "  *                                                                *"
    echo "  *      sudo dasi-install                                         *"
    echo "  *                                                                *"
    echo "  *  Do NOT remove the stick while running from it.                *"
    echo "  ******************************************************************"
    echo
fi
