import Gio from 'gi://Gio';
import GdkPixbuf from 'gi://GdkPixbuf';
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
const LAYOUT_PATH = GLib.build_filenamev([GLib.get_home_dir(), '.config', 'robobloq-led', 'layout.json']);
const BLACK_FALLBACK = [77, 77, 77]; // White at 30 percent brightness.
const PARIS = {latitude: 48.8566, longitude: 2.3522};
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

function solarTime(date, latitude, longitude, sunrise) {
    const yearStart = new Date(date.getFullYear(), 0, 0);
    const day = Math.floor((date - yearStart) / 86400000);
    const longitudeHour = longitude / 15;
    const approximateTime = day + ((sunrise ? 6 : 18) - longitudeHour) / 24;
    const meanAnomaly = 0.9856 * approximateTime - 3.289;
    let sunLongitude = meanAnomaly + 1.916 * Math.sin(meanAnomaly * Math.PI / 180) +
        0.020 * Math.sin(2 * meanAnomaly * Math.PI / 180) + 282.634;
    sunLongitude = (sunLongitude + 360) % 360;
    let rightAscension = Math.atan(0.91764 * Math.tan(sunLongitude * Math.PI / 180)) * 180 / Math.PI;
    rightAscension = (rightAscension + 360) % 360;
    rightAscension += Math.floor(sunLongitude / 90) * 90 - Math.floor(rightAscension / 90) * 90;
    rightAscension /= 15;
    const sinDeclination = 0.39782 * Math.sin(sunLongitude * Math.PI / 180);
    const cosDeclination = Math.cos(Math.asin(sinDeclination));
    const cosHour = (Math.cos(90.833 * Math.PI / 180) - sinDeclination * Math.sin(latitude * Math.PI / 180)) /
        (cosDeclination * Math.cos(latitude * Math.PI / 180));
    if (cosHour < -1 || cosHour > 1)
        return null;
    let hour = Math.acos(cosHour) * 180 / Math.PI;
    if (sunrise)
        hour = 360 - hour;
    hour /= 15;
    const universalTime = (hour + rightAscension - 0.06571 * approximateTime - 6.622 - longitudeHour + 24) % 24;
    const localTime = (universalTime - date.getTimezoneOffset() / 60 + 24) % 24;
    return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, Math.round(localTime * 60));
}

export default class RobobloqLedExtension extends Extension {
    enable() {
        this._syncEnabled = false;
        this._manualOverride = false;
        this._solarTimer = null;
        this._updatingSyncSwitch = false;
        this._wallpaperMonitor = null;
        this._wallpaperMonitorPath = null;
        this._wallpaperUpdateTimer = null;
        this._displays = this._loadDisplays();
        this._backgroundSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.background'});
        this._interfaceSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._wallpaperChangedId = this._backgroundSettings.connect('changed', () => this._resetWallpaperMonitor());
        this._colorSchemeChangedId = this._interfaceSettings.connect('changed::color-scheme', () => this._resetWallpaperMonitor());
        this._resetWallpaperMonitor();
        this._indicator = new PanelMenu.Button(0.0, 'ROBOBLOQ LED');
        this._indicator.add_child(new St.Label({
            text: 'LED',
        }));

        this._sync = new PopupMenu.PopupSwitchMenuItem(t('Synchronisation écran'), false);
        this._sync.connect('toggled', (_item, enabled) => {
            if (!this._updatingSyncSwitch)
                this._setSyncEnabled(enabled, true);
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
            this._setSyncEnabled(false, true);
            callDaemon('Off');
        });

        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addAction(t('Préférences'), () => this.openPreferences());

        Main.panel.addToStatusArea(this.uuid, this._indicator);
        this._configureSolarSchedule();
    }

    _setFixedColor(r, g, b, brightness) {
        this._setSyncEnabled(false, true);
        callDaemon('SetColor', new GLib.Variant('(iiii)', [r, g, b, brightness]));
    }

    _addEffect(menu, label, effectId, icon = null) {
        const item = icon
            ? new PopupMenu.PopupImageMenuItem(label, icon)
            : new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => {
            this._setSyncEnabled(false, true);
            callDaemon('StartHardwareEffect', new GLib.Variant('(i)', [effectId]));
        });
        menu.addMenuItem(item);
    }

    _addRhythm(menu, label, effectId, icon) {
        const item = new PopupMenu.PopupImageMenuItem(label, icon);
        item.connect('activate', () => {
            this._setSyncEnabled(false, true);
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

    _setSyncEnabled(enabled, manual) {
        if (manual)
            this._manualOverride = true;
        if (enabled)
            this._startSync();
        else
            this._stopSync();
        if (this._sync.state !== enabled) {
            this._updatingSyncSwitch = true;
            this._sync.setToggleState(enabled);
            this._updatingSyncSwitch = false;
        }
    }

    _startSync() {
        this._displays = this._loadDisplays();
        if (this._displays.length === 0) {
            console.warn('ROBOBLOQ LED has no configured displays for screen synchronization');
            this._sync.setToggleState(false);
            return;
        }
        this._syncEnabled = true;
        callDaemon('Stop');
        this._updateWallpaperColors();
    }

    _stopSync() {
        this._syncEnabled = false;
    }

    _loadDisplays() {
        try {
            const [success, contents] = Gio.File.new_for_path(LAYOUT_PATH).load_contents(null);
            if (!success)
                throw new Error('could not read layout');
            const displays = JSON.parse(new TextDecoder().decode(contents)).displays;
            if (!Array.isArray(displays))
                throw new Error('layout has no displays');
            return displays;
        } catch (error) {
            console.warn(`ROBOBLOQ LED cannot load ${LAYOUT_PATH}: ${error.message}`);
            return [];
        }
    }

    _loadSchedule() {
        try {
            const [, contents] = Gio.File.new_for_path(LAYOUT_PATH).load_contents(null);
            const schedule = JSON.parse(new TextDecoder().decode(contents)).schedule ?? {};
            return {
                enabled: schedule.enabled === true,
                latitude: Number.isFinite(schedule.latitude) ? schedule.latitude : PARIS.latitude,
                longitude: Number.isFinite(schedule.longitude) ? schedule.longitude : PARIS.longitude,
            };
        } catch (_error) {
            return {...PARIS, enabled: true};
        }
    }

    _configureSolarSchedule() {
        if (this._solarTimer !== null) {
            GLib.Source.remove(this._solarTimer);
            this._solarTimer = null;
        }
        const schedule = this._loadSchedule();
        if (!schedule.enabled)
            return;
        const now = new Date();
        const sunrise = solarTime(now, schedule.latitude, schedule.longitude, true);
        const sunset = solarTime(now, schedule.latitude, schedule.longitude, false);
        if (!sunrise || !sunset)
            return;
        if (!this._manualOverride)
            this._setSyncEnabled(now < sunrise || now >= sunset, false);
        const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
        const next = now < sunrise ? sunrise : now < sunset ? sunset : solarTime(
            tomorrow, schedule.latitude, schedule.longitude, true);
        this._solarTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.max(1000, next - now + 1000), () => {
            this._manualOverride = false;
            this._configureSolarSchedule();
            return GLib.SOURCE_REMOVE;
        });
    }

    _monitorForDisplay(display) {
        const monitors = [...Main.layoutManager.monitors].sort((first, second) =>
            first.x - second.x || first.y - second.y);
        return monitors[display.screen === 'right' ? 1 : 0] ?? null;
    }

    _updateWallpaperColors() {
        if (!this._syncEnabled)
            return;
        try {
            const path = this._wallpaperPath();
            const image = GdkPixbuf.Pixbuf.new_from_file(path);
            const monitors = [...Main.layoutManager.monitors];
            const left = Math.min(...monitors.map(monitor => monitor.x));
            const top = Math.min(...monitors.map(monitor => monitor.y));
            const right = Math.max(...monitors.map(monitor => monitor.x + monitor.width));
            const bottom = Math.max(...monitors.map(monitor => monitor.y + monitor.height));
            const colors = this._displays.map(display => {
                const monitor = this._monitorForDisplay(display);
                if (!monitor)
                    throw new Error(`no monitor available for ${display.screen}`);
                return this._averageWallpaperArea(image, monitor, left, top, right - left, bottom - top);
            });
            console.log(`ROBOBLOQ LED wallpaper colors: ${JSON.stringify(colors)}`);
            callDaemon('SetScreenColors', new GLib.Variant('(a(iii))', [colors]));
        } catch (error) {
            console.warn(`ROBOBLOQ LED wallpaper sample failed: ${error.message}`);
        }
    }

    _wallpaperPath() {
        const dark = this._interfaceSettings.get_string('color-scheme') === 'prefer-dark';
        const uri = dark && this._backgroundSettings.get_string('picture-uri-dark') ||
            this._backgroundSettings.get_string('picture-uri');
        const path = Gio.File.new_for_uri(uri).get_path();
        if (!path)
            throw new Error('the configured wallpaper is not a local file');
        return path;
    }

    _resetWallpaperMonitor() {
        let path;
        try {
            path = this._wallpaperPath();
        } catch (error) {
            console.warn(`ROBOBLOQ LED wallpaper monitor failed: ${error.message}`);
            return;
        }
        if (path === this._wallpaperMonitorPath) {
            this._queueWallpaperUpdate();
            return;
        }
        this._wallpaperMonitor?.cancel();
        this._wallpaperMonitorPath = path;
        this._wallpaperMonitor = Gio.File.new_for_path(GLib.path_get_dirname(path)).monitor_directory(
            Gio.FileMonitorFlags.NONE, null);
        this._wallpaperMonitor.connect('changed', (_monitor, file, otherFile) => {
            if (file.get_path() === path || otherFile?.get_path() === path)
                this._queueWallpaperUpdate();
        });
        this._queueWallpaperUpdate();
    }

    _queueWallpaperUpdate() {
        if (!this._syncEnabled || this._wallpaperUpdateTimer !== null)
            return;
        this._wallpaperUpdateTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
            this._wallpaperUpdateTimer = null;
            this._updateWallpaperColors();
            return GLib.SOURCE_REMOVE;
        });
    }

    _averageWallpaperArea(image, monitor, desktopLeft, desktopTop, desktopWidth, desktopHeight) {
        const imageX = Math.floor((monitor.x - desktopLeft) * image.width / desktopWidth);
        const imageY = Math.floor((monitor.y - desktopTop) * image.height / desktopHeight);
        const imageWidth = Math.max(1, Math.ceil(monitor.width * image.width / desktopWidth));
        const imageHeight = Math.max(1, Math.ceil(monitor.height * image.height / desktopHeight));
        const pixels = image.get_pixels();
        const channels = image.get_n_channels();
        const stride = image.get_rowstride();
        const total = [0, 0, 0];
        let count = 0;
        for (let y = imageY; y < Math.min(image.height, imageY + imageHeight); y += Math.max(1, Math.floor(imageHeight / 48))) {
            for (let x = imageX; x < Math.min(image.width, imageX + imageWidth); x += Math.max(1, Math.floor(imageWidth / 48))) {
                const offset = y * stride + x * channels;
                total[0] += pixels[offset];
                total[1] += pixels[offset + 1];
                total[2] += pixels[offset + 2];
                count++;
            }
        }
        const color = total.map(value => Math.round(value / count));
        return color.every(value => value < 5) ? BLACK_FALLBACK : color;
    }

    _addAction(label, callback) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', callback);
        this._indicator.menu.addMenuItem(item);
    }

    disable() {
        this._stopSync();
        if (this._wallpaperUpdateTimer !== null)
            GLib.Source.remove(this._wallpaperUpdateTimer);
        this._wallpaperMonitor?.cancel();
        this._wallpaperMonitor = null;
        if (this._wallpaperChangedId)
            this._backgroundSettings.disconnect(this._wallpaperChangedId);
        if (this._colorSchemeChangedId)
            this._interfaceSettings.disconnect(this._colorSchemeChangedId);
        this._backgroundSettings = null;
        this._interfaceSettings = null;
        if (this._solarTimer !== null)
            GLib.Source.remove(this._solarTimer);
        this._indicator?.destroy();
        this._indicator = null;
    }
}
