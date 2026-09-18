import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const LAYOUT_PATH = GLib.build_filenamev([GLib.get_home_dir(), '.config', 'robobloq-led', 'layout.json']);
const BASE_HEX = '5242100e86010000ff4142000000feb9' +
    '00000000000000000000000000000000'.repeat(3);
const EN = {
    'Synchroniser le fond d\'ecran': 'Synchronize wallpaper', 'Effets': 'Effects',
    'Rythme contrôleur': 'Controller rhythm', 'Blanc chaud': 'Warm white',
    'Bleu doux': 'Soft blue', 'Eteindre': 'Turn off', 'Préférences': 'Preferences',
    'Vitesse': 'Speed', 'Serpentin': 'Snake', 'Feu': 'Fire', 'Meteore': 'Meteor',
    'Scintillement': 'Twinkle', 'Dégradé': 'Gradient', 'Defilement': 'Scrolling',
    'Onde': 'Wave', 'Pulsation': 'Pulse', 'Spectre': 'Spectrum',
    'Chenillard': 'Chaser', 'Arc-en-ciel': 'Rainbow',
};
const t = text => (GLib.getenv('LANGUAGE') || GLib.getenv('LC_ALL') || GLib.getenv('LC_MESSAGES') || GLib.getenv('LANG') || '').startsWith('fr') ? text : (EN[text] || text);
const DYNAMIC_EFFECTS = [
    ['Dynamix', 'dxlight-dynamix', 'weather-clear-symbolic'],
    ['Serpentin', 'dxlight-serpentin', 'weather-few-clouds-symbolic'],
    ['Feu', 'dxlight-feu', 'weather-storm-symbolic'],
    ['Meteore', 'dxlight-4', 'weather-showers-scattered-symbolic'],
    ['Scintillement', 'dxlight-5', 'starred-symbolic'],
    ['Dégradé', 'dxlight-6', 'color-select-symbolic'],
    ['Defilement', 'dxlight-7', 'view-conceal-symbolic'],
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

function reportFromHex(hex) {
    const report = new Uint8Array(hex.length / 2);
    for (let index = 0; index < report.length; index++)
        report[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16);
    return report;
}

export default class RobobloqLedExtension extends Extension {
    enable() {
        this._counters = new Map();
        this._devicePaths = this._loadDevicePaths();
        this._indicator = new PanelMenu.Button(0.0, 'ROBOBLOQ LED');
        this._indicator.add_child(new St.Label({
            text: 'LED',
        }));

        this._dxlightSpeed = 50;
        const effects = new PopupMenu.PopupSubMenuMenuItem(t('Effets'));
        DYNAMIC_EFFECTS.forEach(([label, effect, icon], index) =>
            this._addEffect(effects.menu, label, index, icon, false));
        effects.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addSpeedControl(effects.menu);

        const rhythm = new PopupMenu.PopupSubMenuMenuItem(t('Rythme contrôleur'));
        RHYTHM_EFFECTS.forEach(([label, icon], index) =>
            this._addEffect(rhythm.menu, label, index, icon, true));
        this._indicator.menu.addMenuItem(effects);
        this._indicator.menu.addMenuItem(rhythm);

        this._addAction(t('Blanc chaud'), () => this._setFixedColor(255, 200, 120, 30));
        this._addAction(t('Bleu doux'), () => this._setFixedColor(10, 132, 255, 35));
        this._addAction(t('Eteindre'), () => {
            this._stopHardwareEffect();
            this._setColor(0, 0, 0);
        });

        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addAction(t('Préférences'), () => this.openPreferences());

        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    _setFixedColor(r, g, b, brightness) {
        this._stopHardwareEffect();
        this._setColor(Math.round(r * brightness / 100), Math.round(g * brightness / 100), Math.round(b * brightness / 100));
    }

    _addEffect(menu, label, effectId, icon = null, rhythm = false) {
        const item = icon
            ? new PopupMenu.PopupImageMenuItem(label, icon)
            : new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => {
            if (rhythm)
                this._setRhythmEffect(effectId);
            else
                this._setHardwareEffect(effectId);
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
            this._setHardwareSpeed(speed);
        });
        item.add_child(label);
        item.add_child(this._speedSlider);
        item.add_child(this._speedValue);
        menu.addMenuItem(item);
    }

    _loadDevicePaths() {
        try {
            const [success, contents] = Gio.File.new_for_path(LAYOUT_PATH).load_contents(null);
            if (!success)
                throw new Error('could not read file');
            const layout = JSON.parse(new TextDecoder().decode(contents));
            if (!Array.isArray(layout.displays))
                throw new Error('"displays" is not an array');
            const paths = layout.displays
                .map(display => display?.device)
                .filter(path => typeof path === 'string' && path.startsWith('/dev/'));
            return [...new Set(paths)];
        } catch (error) {
            // Do not guess HID interfaces: a fallback scan could target unrelated devices.
            console.warn(`ROBOBLOQ LED cannot load configured devices from ${LAYOUT_PATH}: ${error.message}`);
            return [];
        }
    }

    _nextCounter(device) {
        const counter = this._counters.get(device) ?? 0x0e;
        this._counters.set(device, (counter + 1) & 0xff);
        return counter;
    }

    _sendReports(buildReport) {
        if (this._devicePaths.length === 0) {
            console.warn('ROBOBLOQ LED has no configured HID device paths; no report was sent');
            return;
        }

        for (const device of this._devicePaths) {
            const report = buildReport(this._nextCounter(device));
            try {
                const stream = Gio.File.new_for_path(device).append_to(Gio.FileCreateFlags.NONE, null);
                const [written, count] = stream.write_all(report, null);
                stream.close(null);
                if (!written || count !== 64)
                    throw new Error(`wrote ${count} bytes instead of 64`);
            } catch (error) {
                console.warn(`ROBOBLOQ LED opening ${device} for raw HID output failed: ${error.message}`);
            }
        }
    }

    _setColor(r, g, b) {
        this._sendReports(counter => {
            const report = reportFromHex(BASE_HEX);
            report[3] = counter;
            report[6] = r & 0xff;
            report[7] = g & 0xff;
            report[8] = b & 0xff;
            report[15] = report.slice(0, 15).reduce((sum, byte) => sum + byte, 0) & 0xff;
            return report;
        });
    }

    _setHardwareEffect(effectId) {
        this._sendReports(counter => this._effectReport(counter, 0x02, effectId));
    }

    _setRhythmEffect(effectId) {
        this._sendReports(counter => this._effectReport(counter, 0x03, effectId));
    }

    _effectReport(counter, mode, effectId) {
        const report = new Uint8Array(64);
        report.set([0x52, 0x42, 0x08, counter, 0x85, mode, effectId]);
        report[7] = report.slice(0, 7).reduce((sum, byte) => sum + byte, 0) & 0xff;
        return report;
    }

    _setHardwareSpeed(speed) {
        this._sendReports(counter => {
            const report = new Uint8Array(64);
            report.set([0x52, 0x42, 0x07, counter, 0x8a, 100 - speed]);
            report[6] = report.slice(0, 6).reduce((sum, byte) => sum + byte, 0) & 0xff;
            return report;
        });
    }

    _stopHardwareEffect() {
        this._sendReports(counter => {
            const report = new Uint8Array(64);
            report.set([0x52, 0x42, 0x06, counter, 0x97, 0x00]);
            report[6] = report.slice(0, 6).reduce((sum, byte) => sum + byte, 0) & 0xff;
            return report;
        });
    }

    _addAction(label, callback) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', callback);
        this._indicator.menu.addMenuItem(item);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
        this._counters = null;
        this._devicePaths = null;
    }
}
