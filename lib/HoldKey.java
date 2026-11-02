import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;
import java.lang.reflect.Method;

/**
 * Holds a key the way a remote's button held down does, for tv-remote.sh --long-press before API 30:
 * the key goes down; at <repeat ms> a repeat of it, flagged as a long press; at <release ms> the
 * release. All three carry the down time of the first, as a real key's do. Injected as the shell
 * user (run with app_process), into the device's input, so the window in front gets them as it
 * gets a keyboard's, without anything else running beside it (monkey, which this replaces, made
 * the windows opened while it ran miss their focus on API 28).
 *
 * Usage: app_process /system/bin HoldKey <Android key code> <repeat ms> <release ms>
 * Prints "Events injected: 3" when it sent them.
 *
 * Built into HoldKey.dex by build-hold-key.sh, which also says what it needs; rebuild it after
 * changing this file.
 */
public class HoldKey {
    public static void main(String[] args) throws Exception {
        int code = Integer.parseInt(args[0]);
        long repeatAt = Long.parseLong(args[1]);
        long releaseAt = Long.parseLong(args[2]);
        Object manager;
        Method inject;
        try {
            // Android 14 and later keep the injection in a class of its own.
            Class<?> global = Class.forName("android.hardware.input.InputManagerGlobal");
            manager = global.getMethod("getInstance").invoke(null);
            inject = global.getMethod("injectInputEvent", InputEvent.class, int.class);
        } catch (ClassNotFoundException e) {
            Class<?> input = Class.forName("android.hardware.input.InputManager");
            manager = input.getMethod("getInstance").invoke(null);
            inject = input.getMethod("injectInputEvent", InputEvent.class, int.class);
        }
        long down = SystemClock.uptimeMillis();
        KeyEvent press = new KeyEvent(down, down, KeyEvent.ACTION_DOWN, code, 0, 0,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD);
        inject.invoke(manager, press, 0);   // INJECT_INPUT_EVENT_MODE_ASYNC
        sleepUntil(down + repeatAt);
        inject.invoke(manager, KeyEvent.changeTimeRepeat(press, SystemClock.uptimeMillis(), 1, KeyEvent.FLAG_LONG_PRESS), 0);
        sleepUntil(down + releaseAt);
        KeyEvent release = KeyEvent.changeAction(KeyEvent.changeTimeRepeat(press, SystemClock.uptimeMillis(), 0), KeyEvent.ACTION_UP);
        inject.invoke(manager, release, 0);
        System.out.println("Events injected: 3");
    }

    private static void sleepUntil(long uptime) throws InterruptedException {
        long wait = uptime - SystemClock.uptimeMillis();
        if (wait > 0) Thread.sleep(wait);
    }
}
