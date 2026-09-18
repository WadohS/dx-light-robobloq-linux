import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import GLib from 'gi://GLib';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const EN = {
    'DX-Light Configuration': 'DX-Light Configuration', 'Chargement des contrôleurs...': 'Loading controllers...',
    'Service LED indisponible': 'LED service unavailable', 'Démarre robobloq-led.service puis rouvre cette fenêtre.': 'Start robobloq-led.service, then reopen this window.',
    'Configuration indisponible': 'Configuration unavailable', 'Un bandeau par écran. Répartis les LEDs sur trois ou quatre côtés selon la pose réelle.': 'One strip per display. Distribute LEDs over three or four sides according to the physical installation.',
    'Les réglages sont enregistrés localement et lus au prochain démarrage du service.': 'Settings are saved locally and read when the service next starts.',
    'Appliquer la configuration': 'Apply configuration', 'Enregistrer': 'Save', 'Modifications non enregistrées': 'Unsaved changes',
    'Enregistrement...': 'Saving...', 'Enregistrement impossible': 'Unable to save', 'Configuration enregistrée': 'Configuration saved',
    'Les réglages seront lus au prochain redémarrage du service.': 'Settings will be read when the service next restarts.',
    'Session GNOME': 'GNOME session', 'Éteindre les LEDs': 'Turn off LEDs', 'Synchronisation fond': 'Wallpaper synchronization', 'Effet DX-Light': 'DX-Light effect',
    'Au verrouillage': 'On lock', 'Au déverrouillage': 'On unlock', 'Contrôleur': 'Controller', 'Écran': 'Display', 'Écran gauche': 'Left display', 'Écran droit': 'Right display',
    'Derrière l’écran': 'Behind display', 'Au-dessus de l’écran': 'Above display', 'Sous l’écran': 'Below display', 'À gauche de l’écran': 'Left of display', 'À droite de l’écran': 'Right of display',
    'De gauche vers la droite': 'Left to right', 'De droite vers la gauche': 'Right to left', 'Bord de l’écran': 'Display edge', 'Centre de l’écran': 'Display center',
    '3 côtés': '3 sides', '4 côtés': '4 sides', 'LEDs à gauche': 'Left LEDs', 'LEDs en haut': 'Top LEDs', 'LEDs à droite': 'Right LEDs', 'LEDs en bas': 'Bottom LEDs',
    'Planification solaire': 'Solar schedule', 'Activer la synchronisation du coucher au lever': 'Enable synchronization from sunset to sunrise', 'Latitude': 'Latitude', 'Longitude': 'Longitude',
};
const t = text => (GLib.getenv('LANGUAGE') || GLib.getenv('LC_ALL') || GLib.getenv('LC_MESSAGES') || GLib.getenv('LANG') || '').startsWith('fr') ? text : (EN[text] || text);
const EDGE_KEYS = ['left', 'top', 'right', 'bottom'];
const SESSION_EFFECTS = [
    ['dxlight-dynamix', 'Dynamix'], ['dxlight-serpentin', 'Serpentin'], ['dxlight-feu', 'Feu'],
    ['dxlight-4', 'Météore'], ['dxlight-5', 'Scintillement'], ['dxlight-6', 'Dégradé'], ['dxlight-7', 'Défilement'],
    ...Array.from({length: 7}, (_value, index) => [`dxlight-rhythm-${index}`, `Rythme ${index + 1}`]),
];

const LAYOUT_PATH = GLib.build_filenamev([GLib.get_home_dir(), '.config', 'robobloq-led', 'layout.json']);

function defaultSession() {
    return {
        lock: {mode: 'off', effect: 'dxlight-dynamix'},
        unlock: {mode: 'sync', effect: 'dxlight-dynamix'},
    };
}

function isVendorDevice(path) {
    const sysname = GLib.path_get_basename(path);
    if (!/^hidraw\d+$/.test(sysname))
        return false;
    try {
        const [, descriptor] = Gio.File.new_for_path(`/sys/class/hidraw/${sysname}/device/report_descriptor`).load_contents(null);
        return descriptor[0] === 0x06 && descriptor[1] === 0x00 && descriptor[2] === 0xff;
    } catch (_error) {
        return false;
    }
}

function discoverDevices() {
    const directoryPath = '/dev/input/by-path';
    const devices = [];
    try {
        const directory = Gio.File.new_for_path(directoryPath);
        const entries = directory.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let entry;
        while ((entry = entries.next_file(null)) !== null) {
            const name = entry.get_name();
            if (!name.endsWith('-hidraw'))
                continue;
            const link = GLib.build_filenamev([directoryPath, name]);
            const target = GLib.canonicalize_filename(GLib.file_read_link(link), directoryPath);
            if (!/^\/dev\/hidraw\d+$/.test(target) || !GLib.file_test(target, GLib.FileTest.EXISTS) || !isVendorDevice(target))
                continue;
            devices.push({link, target});
        }
        entries.close(null);
    } catch (_error) {
        // An absent by-path directory simply means no stable controller links are available.
    }
    const selected = new Set();
    return devices.sort((first, second) => first.link.includes('-usbv') - second.link.includes('-usbv'))
        .filter(device => !selected.has(device.target) && selected.add(device.target))
        .map(device => device.link);
}

function defaultLayout(devices) {
    return {
        version: 2,
        session: defaultSession(),
        schedule: {enabled: true, latitude: 48.8566, longitude: 2.3522},
        displays: devices.map((device, index) => ({
            device,
            screen: index === 0 ? 'left' : 'right',
            location: 'back',
            installation_direction: index === 0 ? 'left-to-right' : 'right-to-left',
            sync_area: 'edge',
            edge_count: 3,
            zones: {left: 17, top: 29, right: 17, bottom: 0},
        })),
    };
}

function loadLayout(devices, callback) {
    const layoutFile = Gio.File.new_for_path(LAYOUT_PATH);
    layoutFile.load_contents_async(null, (source, result) => {
        try {
            const [, contents] = source.load_contents_finish(result);
            const layout = JSON.parse(new TextDecoder().decode(contents));
            if (!layout || !Array.isArray(layout.displays))
                throw new Error('Invalid layout');
            const defaults = defaultSession();
            layout.session = layout.session && typeof layout.session === 'object' ? layout.session : {};
            layout.session.lock = layout.session.lock && typeof layout.session.lock === 'object' ? layout.session.lock : defaults.lock;
            layout.session.unlock = layout.session.unlock && typeof layout.session.unlock === 'object' ? layout.session.unlock : defaults.unlock;
            layout.schedule = layout.schedule && typeof layout.schedule === 'object' ? layout.schedule : {enabled: true, latitude: 48.8566, longitude: 2.3522};
            callback(null, layout);
        } catch (_error) {
            callback(null, defaultLayout(devices));
        }
    });
}

function saveLayout(layout, callback) {
    const layoutFile = Gio.File.new_for_path(LAYOUT_PATH);
    GLib.mkdir_with_parents(GLib.path_get_dirname(LAYOUT_PATH), 0o700);
    const contents = new TextEncoder().encode(`${JSON.stringify(layout, null, 2)}\n`);
    layoutFile.replace_contents_async(contents, null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null, (source, result) => {
        try {
            source.replace_contents_finish(result);
            callback(null);
        } catch (error) {
            callback(error);
        }
    });
}

function deviceLabel(path) {
    const match = path.match(/usb-[^:]+:(.+)-hidraw$/);
    return match ? `${t('Contrôleur')} USB ${match[1]}` : path;
}

function dropdown(labels, selected) {
    const widget = Gtk.DropDown.new_from_strings(labels.map(t));
    widget.set_selected(Math.max(0, selected));
    return widget;
}

function addRow(group, title, subtitle, widget) {
    const row = new Adw.ActionRow({title, subtitle});
    row.add_suffix(widget);
    group.add(row);
}

export default class RobobloqLedPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        window.set_default_size(680, 760);
        const page = new Adw.PreferencesPage({title: 'DX-Light Configuration'});
        const loading = new Adw.PreferencesGroup({title: 'DX-Light Configuration'});
        loading.add(new Adw.ActionRow({title: 'Chargement des contrôleurs...'}));
        page.add(loading);
        window.add(page);

        const devices = discoverDevices();
        loadLayout(devices, (_error, layout) => {
            page.remove(loading);
            this._build(page, devices, layout);
        });
    }

    _build(page, devices, layout) {
        const configuredDevices = [...new Set([
            ...devices,
            ...layout.displays.map(display => display.device).filter(device => typeof device === 'string'),
        ])];
        const intro = new Adw.PreferencesGroup({
            title: 'DX-Light Configuration',
            description: 'Un bandeau par écran. Répartis les LEDs sur trois ou quatre côtés selon la pose réelle.',
        });
        intro.add(new Adw.ActionRow({
            title: `${configuredDevices.length} contrôleur(s) configuré(s)`,
            subtitle: 'Les réglages sont enregistrés localement et lus au prochain démarrage du service.',
        }));
        page.add(intro);

        const widgets = [];
        for (const [index, display] of layout.displays.entries())
            widgets.push(this._addDisplay(page, index, display, configuredDevices));
        const session = this._addSession(page, layout.session);
        const schedule = this._addSchedule(page, layout.schedule);

        const actions = new Adw.PreferencesGroup();
        const saveRow = new Adw.ActionRow({title: 'Appliquer la configuration'});
        const saveButton = new Gtk.Button({label: 'Enregistrer', valign: Gtk.Align.CENTER});
        saveButton.add_css_class('suggested-action');
        saveRow.add_suffix(saveButton);
        actions.add(saveRow);
        const result = new Adw.ActionRow({title: 'Modifications non enregistrées'});
        actions.add(result);
        page.add(actions);

        saveButton.connect('clicked', () => {
            const displays = widgets.map(display => ({
                device: configuredDevices[display.device.selected],
                screen: display.screen.selected === 0 ? 'left' : 'right',
                location: ['back', 'top', 'bottom', 'left', 'right'][display.location.selected],
                installation_direction: display.direction.selected === 0 ? 'left-to-right' : 'right-to-left',
                sync_area: display.syncArea.selected === 0 ? 'edge' : 'center',
                edge_count: display.edgeCount.selected + 3,
                zones: Object.fromEntries(EDGE_KEYS.map(key => [key, key === 'bottom' && display.edgeCount.selected === 0 ? 0 : display.zones[key].get_value_as_int()])),
            }));
            saveButton.sensitive = false;
            result.title = 'Enregistrement...';
            saveLayout({version: 2, displays, session: this._sessionPayload(session), schedule: this._schedulePayload(schedule)}, error => {
                saveButton.sensitive = true;
                result.title = error ? 'Enregistrement impossible' : 'Configuration enregistrée';
                result.subtitle = error ? error.message : 'Les réglages seront lus au prochain redémarrage du service.';
            });
        });
    }

    _addSession(page, session) {
        const group = new Adw.PreferencesGroup({
            title: 'Session GNOME',
            description: 'Le verrouillage coupe la synchronisation avant de changer les LEDs. Le démarrage automatique du service est conservé.',
        });
        page.add(group);
        const addAction = (title, action) => {
            const mode = dropdown(['Éteindre les LEDs', 'Synchronisation fond', 'Effet DX-Light'], ['off', 'sync', 'effect'].indexOf(action.mode));
            addRow(group, title, null, mode);
            const effect = dropdown(SESSION_EFFECTS.map(([, label]) => label), SESSION_EFFECTS.findIndex(([id]) => id === action.effect));
            addRow(group, `${title} : effet`, 'Utilisé uniquement avec « Effet DX-Light ».', effect);
            const updateEffect = () => effect.sensitive = mode.selected === 2;
            mode.connect('notify::selected', updateEffect);
            updateEffect();
            return {mode, effect};
        };
        return {lock: addAction('Au verrouillage', session.lock), unlock: addAction('Au déverrouillage', session.unlock)};
    }

    _sessionPayload(session) {
        const action = widgets => ({
            mode: ['off', 'sync', 'effect'][widgets.mode.selected],
            effect: SESSION_EFFECTS[widgets.effect.selected][0],
        });
        return {lock: action(session.lock), unlock: action(session.unlock)};
    }

    _addSchedule(page, schedule) {
        const group = new Adw.PreferencesGroup({
            title: 'Planification solaire',
            description: 'La synchronisation s’active au coucher et s’arrête au lever du soleil. Les actions manuelles restent prioritaires jusqu’au prochain événement solaire.',
        });
        page.add(group);
        const enabled = new Gtk.Switch({active: schedule.enabled === true, valign: Gtk.Align.CENTER});
        addRow(group, 'Activer la synchronisation du coucher au lever', null, enabled);
        const latitude = Gtk.SpinButton.new_with_range(-90, 90, 0.0001);
        latitude.set_digits(4);
        latitude.set_value(Number.isFinite(schedule.latitude) ? schedule.latitude : 48.8566);
        addRow(group, 'Latitude', 'Paris : 48.8566', latitude);
        const longitude = Gtk.SpinButton.new_with_range(-180, 180, 0.0001);
        longitude.set_digits(4);
        longitude.set_value(Number.isFinite(schedule.longitude) ? schedule.longitude : 2.3522);
        addRow(group, 'Longitude', 'Paris : 2.3522', longitude);
        return {enabled, latitude, longitude};
    }

    _schedulePayload(schedule) {
        return {
            enabled: schedule.enabled.active,
            latitude: schedule.latitude.get_value(),
            longitude: schedule.longitude.get_value(),
        };
    }

    _addDisplay(page, index, display, devices) {
        const group = new Adw.PreferencesGroup({
            title: `Écran ${display.screen === 'right' ? 'droit' : 'gauche'}`,
            description: 'Configure le trajet du bandeau vu depuis l’avant de l’écran.',
        });
        page.add(group);

        const device = dropdown(devices.map(deviceLabel), devices.indexOf(display.device));
        addRow(group, 'Contrôleur', 'Bandeau USB associé à cet écran.', device);
        const screen = dropdown(['Écran gauche', 'Écran droit'], display.screen === 'right' ? 1 : 0);
        addRow(group, 'Écran', null, screen);
        const location = dropdown(['Derrière l’écran', 'Au-dessus de l’écran', 'Sous l’écran', 'À gauche de l’écran', 'À droite de l’écran'], ['back', 'top', 'bottom', 'left', 'right'].indexOf(display.location));
        addRow(group, 'Pose du bandeau', 'Correspond à « Location » dans DX-Light.', location);
        const direction = dropdown(['De gauche vers la droite', 'De droite vers la gauche'], display.installation_direction === 'right-to-left' ? 1 : 0);
        addRow(group, 'Arrivée des LEDs', 'Sens du câble et du premier point LED sur l’écran.', direction);
        const syncArea = dropdown(['Bord de l’écran', 'Centre de l’écran'], display.sync_area === 'center' ? 1 : 0);
        addRow(group, 'Zone de synchronisation', null, syncArea);
        const edgeCount = dropdown(['3 côtés', '4 côtés'], display.edge_count === 4 ? 1 : 0);
        addRow(group, 'Zones du bandeau', null, edgeCount);

        const zones = {};
        for (const [key, title] of [['left', 'LEDs à gauche'], ['top', 'LEDs en haut'], ['right', 'LEDs à droite'], ['bottom', 'LEDs en bas']]) {
            const count = Gtk.SpinButton.new_with_range(0, 254, 1);
            count.set_value(display.zones[key]);
            addRow(group, title, null, count);
            zones[key] = count;
        }
        const updateBottom = () => {
            const enabled = edgeCount.selected === 1;
            zones.bottom.sensitive = enabled;
            if (!enabled)
                zones.bottom.set_value(0);
        };
        edgeCount.connect('notify::selected', updateBottom);
        updateBottom();
        return {device, screen, location, direction, syncArea, edgeCount, zones};
    }
}
