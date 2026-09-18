import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const API_URL = 'http://127.0.0.1:8000';
const WALLPAPER_SYNC_SERVICE = 'robobloq-wallpaper-sync.service';
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

function post(path, body = null) {
    const args = [
        'curl', '--fail', '--silent', '--show-error', '--request', 'POST',
        `${API_URL}${path}`,
    ];

    if (body !== null)
        args.push('--header', 'Content-Type: application/json', '--data', JSON.stringify(body));

    const process = Gio.Subprocess.new(args, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    process.communicate_utf8_async(null, null, (source, result) => {
        try {
            source.communicate_utf8_finish(result);
        } catch (error) {
            console.warn(`ROBOBLOQ LED request failed: ${error.message}`);
        }
    });
}

export default class RobobloqLedExtension extends Extension {
    enable() {
        this._indicator = new PanelMenu.Button(0.0, 'ROBOBLOQ LED');
        this._indicator.add_child(new St.Label({
            text: 'LED',
        }));

        this._sync = new PopupMenu.PopupSwitchMenuItem(t('Synchroniser le fond d\'ecran'), false);
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
            this._addEffect(rhythm.menu, label, `dxlight-rhythm-${index}`, icon));
        this._indicator.menu.addMenuItem(effects);
        this._indicator.menu.addMenuItem(rhythm);

        this._addAction(t('Blanc chaud'), () => this._setFixedColor(255, 200, 120, 30));
        this._addAction(t('Bleu doux'), () => this._setFixedColor(10, 132, 255, 35));
        this._addAction(t('Eteindre'), () => {
            this._sync.setToggleState(false);
            post('/api/off');
        });

        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addAction(t('Préférences'), () => this.openPreferences());

        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    _setFixedColor(r, g, b, brightness) {
        this._stopSync();
        this._sync.setToggleState(false);
        post('/api/color', {r, g, b, brightness});
    }

    _addEffect(menu, label, effect, icon = null) {
        const item = icon
            ? new PopupMenu.PopupImageMenuItem(label, icon)
            : new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => {
            this._sync.setToggleState(false);
            this._stopSync();
            post('/api/effect/start', {effect});
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
            post('/api/dxlight/speed', {speed});
        });
        item.add_child(label);
        item.add_child(this._speedSlider);
        item.add_child(this._speedValue);
        menu.addMenuItem(item);
    }

    _startSync() {
        post('/api/effect/stop');
        this._setWallpaperSync(true);
    }

    _stopSync() {
        this._setWallpaperSync(false);
    }

    _setWallpaperSync(enabled) {
        const action = enabled ? 'start' : 'stop';
        const process = Gio.Subprocess.new(
            ['systemctl', '--user', action, WALLPAPER_SYNC_SERVICE],
            Gio.SubprocessFlags.STDERR_PIPE
        );
        process.wait_async(null, (source, result) => {
            try {
                source.wait_finish(result);
            } catch (error) {
                console.warn(`ROBOBLOQ wallpaper sync failed: ${error.message}`);
            }
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
    }
}
