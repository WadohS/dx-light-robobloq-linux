import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.wadohs.RobobloqLed';
const OBJECT_PATH = '/io/github/wadohs/RobobloqLed';
const INTERFACE_NAME = BUS_NAME;
const EN = {
    'Synchronisation écran': 'Screen synchronization', 'Effets': 'Effects',
    'Rythme contrôleur': 'Controller rhythm', 'Blanc chaud': 'Warm white',
    'Bleu doux': 'Soft blue', 'Eteindre': 'Turn off', 'Préférences': 'Preferences',
    'Vitesse': 'Speed', 'Serpentin': 'Snake', 'Feu': 'Fire', 'Meteore': 'Meteor',
    'Scintillement': 'Twinkle', 'Dégradé': 'Gradient', 'Defilement': 'Scrolling',
    'Onde': 'Wave', 'Pulsation': 'Pulse', 'Spectre': 'Spectrum',
    'Chenillard': 'Chaser', 'Arc-en-ciel': 'Rainbow',
};
const t = text => (GLib.getenv('LANGUAGE') || GLib.getenv('LC_ALL') || GLib.getenv('LC_MESSAGES') || GLib.getenv('LANG') || '').startsWith('fr') ? text : (EN[text] || text);
const DYNAMIC_EFFECTS = [
    ['Dynamix', 0, 'weather-clear-symbolic'],
    ['Serpentin', 1, 'weather-few-clouds-symbolic'],
    ['Feu', 2, 'weather-storm-symbolic'],
    ['Meteore', 3, 'weather-showers-scattered-symbolic'],
    ['Scintillement', 4, 'starred-symbolic'],
    ['Dégradé', 5, 'color-select-symbolic'],
    ['Defilement', 6, 'view-conceal-symbolic'],
];
const RHYTHM_EFFECTS = [
    ['Onde', 'weather-few-clouds-symbolic'],
    ['Pulsation', 'media-playback-start-symbolic'],
    ['Spectre', 'media-eject-symbolic'],
    ['Flash', 'weather-storm-symbolic'],
    ['Dégradé', 'color-select-symbolic'],
    ['Chenillard', 'view-conceal-symbolic'],
    ['Arc-en-ciel', 'weather-clear-symbolic'],
];

function callDaemon(method, parameters = new GLib.Variant('()', [])) {
    Gio.DBus.session.call(
        BUS_NAME,
        OBJECT_PATH,
        INTERFACE_NAME,
        method,
        parameters,
        null,
        Gio.DBusCallFlags.NONE,
        -1,
        null,
        (source, result) => {
            try {
                source.call_finish(result);
            } catch (error) {
                console.warn(`ROBOBLOQ LED D-Bus ${method} failed: ${error.message}`);
            }
        }
    );
}

export default class RobobloqLedExtension extends Extension {
    enable() {
        this._indicator = new PanelMenu.Button(0.0, 'ROBOBLOQ LED');
        this._indicator.add_child(new St.Label({
            text: 'LED',
        }));

        this._sync = new PopupMenu.PopupSwitchMenuItem(t('Synchronisation écran'), false);
        this._sync.connect('toggled', (_item, enabled) => {
            if (enabled)
                this._startSync();
            else
                this._stopSync();
        });
        this._indicator.menu.addMenuItem(this._sync);

        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._dxlightSpeed = 50;
        const effects = new PopupMenu.PopupSubMenuMenuItem(t('Effets'));
        DYNAMIC_EFFECTS.forEach(([label, effect, icon]) =>
            this._addEffect(effects.menu, label, effect, icon));
        effects.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addSpeedControl(effects.menu);

        const rhythm = new PopupMenu.PopupSubMenuMenuItem(t('Rythme contrôleur'));
        RHYTHM_EFFECTS.forEach(([label, icon], index) =>
            this._addRhythm(rhythm.menu, label, index, icon));
        this._indicator.menu.addMenuItem(effects);
        this._indicator.menu.addMenuItem(rhythm);

        this._addAction(t('Blanc chaud'), () => this._setFixedColor(255, 200, 120, 30));
        this._addAction(t('Bleu doux'), () => this._setFixedColor(10, 132, 255, 35));
        this._addAction(t('Eteindre'), () => {
            this._sync.setToggleState(false);
            callDaemon('Off');
        });

        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addAction(t('Préférences'), () => this.openPreferences());

        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    _setFixedColor(r, g, b, brightness) {
        this._stopSync();
        this._sync.setToggleState(false);
        callDaemon('SetColor', new GLib.Variant('(iiii)', [r, g, b, brightness]));
    }

    _addEffect(menu, label, effectId, icon = null) {
        const item = icon
            ? new PopupMenu.PopupImageMenuItem(label, icon)
            : new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => {
            this._sync.setToggleState(false);
            this._stopSync();
            callDaemon('StartHardwareEffect', new GLib.Variant('(i)', [effectId]));
        });
        menu.addMenuItem(item);
    }

    _addRhythm(menu, label, effectId, icon) {
        const item = new PopupMenu.PopupImageMenuItem(label, icon);
        item.connect('activate', () => {
            this._sync.setToggleState(false);
            this._stopSync();
            callDaemon('StartRhythm', new GLib.Variant('(i)', [effectId]));
        });
        menu.addMenuItem(item);
    }

    _addSpeedControl(menu) {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false});
        const label = new St.Label({text: t('Vitesse')});
        this._speedValue = new St.Label({text: `${this._dxlightSpeed}%`});
        this._speedSlider = new Slider.Slider(this._dxlightSpeed / 100);
        this._speedSlider.x_expand = true;
        this._speedSlider.connect('notify::value', slider => {
            const speed = Math.round(slider.value * 20) * 5;
            if (speed === this._dxlightSpeed)
                return;
            this._dxlightSpeed = speed;
            this._speedValue.text = `${speed}%`;
            callDaemon('SetSpeed', new GLib.Variant('(i)', [speed]));
        });
        item.add_child(label);
        item.add_child(this._speedSlider);
        item.add_child(this._speedValue);
        menu.addMenuItem(item);
    }

    _startSync() {
        callDaemon('StartScreenSync', new GLib.Variant('(iiiidi)', [1, 60, 80, 4, 0.35, 6]));
    }

    _stopSync() {
        callDaemon('StopScreenSync');
    }

    _addAction(label, callback) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', callback);
        this._indicator.menu.addMenuItem(item);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
