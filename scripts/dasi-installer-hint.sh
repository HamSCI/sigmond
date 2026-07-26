# /etc/profile.d/dasi-installer-hint.sh — dasi2 USB installation media only
# (gated on the media flag, which dasi-install does not copy to the
# installed system, so this stays silent there).
#
# On the physical console (tty1, where the media's getty drop-in auto-logs
# hamsci in) this LAUNCHES the guided auto-install: one candidate internal
# disk → installs after a 20s abort window, then powers off; ambiguous
# layouts fall back to prompts.  On any other terminal (serial, ssh) it
# only prints the banner — those sessions are for debugging, not for
# surprise disk erasure.
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
    if [ "$(tty 2>/dev/null)" = "/dev/tty1" ]; then
        sudo /usr/local/sbin/dasi-install --auto
    fi
fi
