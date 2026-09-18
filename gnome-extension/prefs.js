import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import GLib from 'gi://GLib';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const LAYOUT_PATH = GLib.build_filenamev([GLib.get_home_dir(), '.config', 'robobloq-led', 'layout.json']);
const EN = {
    'DX-Light Configuration': 'DX-Light Configuration', 'Chargement des contrôleurs...': 'Loading controllers...',
    'Service LED indisponible': 'LED service unavailable', 'Démarre robobloq-led.service puis rouvre cette fenêtre.': 'Start robobloq-led.service, then reopen this window.',
    'Configuration indisponible': 'Configuration unavailable', 'Un bandeau par écran. Répartis les LEDs sur trois ou quatre côtés selon la pose réelle.': 'One strip per display. Distribute LEDs over three or four sides according to the physical installation.',
    'Les réglages sont enregistrés localement et appliqués aux contrôleurs sélectionnés.': 'Settings are saved locally and applied to the selected controllers.',
    'Appliquer la configuration': 'Apply configuration', 'Enregistrer': 'Save', 'Modifications non enregistrées': 'Unsaved changes',
    'Enregistrement et application...': 'Saving and applying...', 'Enregistrement impossible': 'Unable to save', 'Configuration appliquée': 'Configuration applied',
    'Le total de chaque zone a été envoyé à son contrôleur.': 'Each zone total was sent to its controller.',
    'Session GNOME': 'GNOME session', 'Éteindre les LEDs': 'Turn off LEDs', 'Synchronisation fond': 'Wallpaper synchronization', 'Effet DX-Light': 'DX-Light effect',
    'Au verrouillage': 'On lock', 'Au déverrouillage': 'On unlock', 'Contrôleur': 'Controller', 'Écran': 'Display', 'Écran gauche': 'Left display', 'Écran droit': 'Right display',
    'Derrière l’écran': 'Behind display', 'Au-dessus de l’écran': 'Above display', 'Sous l’écran': 'Below display', 'À gauche de l’écran': 'Left of display', 'À droite de l’écran': 'Right of display',
    'De gauche vers la droite': 'Left to right', 'De droite vers la gauche': 'Right to left', 'Bord de l’écran': 'Display edge', 'Centre de l’écran': 'Display center',
    '3 côtés': '3 sides', '4 côtés': '4 sides', 'LEDs à gauche': 'Left LEDs', 'LEDs en haut': 'Top LEDs', 'LEDs à droite': 'Right LEDs', 'LEDs en bas': 'Bottom LEDs',
};
const t = text => (GLib.getenv('LANGUAGE') || GLib.getenv('LC_ALL') || GLib.getenv('LC_MESSAGES') || GLib.getenv('LANG') || '').startsWith('fr') ? text : (EN[text] || text);
const EDGE_KEYS = ['left', 'top', 'right', 'bottom'];
function defaultSession() {
    return {
        lock: {mode: 'off', effect: 'dxlight-dynamix'},
        unlock: {mode: 'sync', effect: 'dxlight-dynamix'},
    };
}

function discoverDevices() {
    const directory = Gio.File.new_for_path('/dev/input/by-path');
    const devices = [];
    try {
        const entries = directory.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let entry;
        while ((entry = entries.next_file(null)) !== null) {
            const name = entry.get_name();
            if (name.endsWith('-hidraw'))
                devices.push(GLib.build_filenamev(['/dev/input/by-path', name]));
        }
        entries.close(null);
    } catch (error) {
        console.warn(`ROBOBLOQ LED cannot discover HID devices: ${error.message}`);
    }
    return devices.sort();
}

function defaultLayout(devices) {
    return {
        version: 2,
        session: defaultSession(),
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

function loadLayout(devices) {
    try {
        const [success, contents] = Gio.File.new_for_path(LAYOUT_PATH).load_contents(null);
        if (!success)
            throw new Error('could not read file');
        const layout = JSON.parse(new TextDecoder().decode(contents));
        if (!layout || !Array.isArray(layout.displays))
            throw new Error('invalid layout');
        if (!layout.session || !layout.session.lock || !layout.session.unlock)
            layout.session = defaultSession();
        return layout;
    } catch (error) {
        if (!error.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
            console.warn(`ROBOBLOQ LED cannot load ${LAYOUT_PATH}: ${error.message}`);
        return defaultLayout(devices);
    }
}

function saveLayout(layout) {
    const directory = Gio.File.new_for_path(GLib.path_get_dirname(LAYOUT_PATH));
    if (!directory.query_exists(null))
        directory.make_directory_with_parents(null);
    const contents = new TextEncoder().encode(`${JSON.stringify(layout, null, 2)}\n`);
    Gio.File.new_for_path(LAYOUT_PATH).replace_contents(
        contents, null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null
    );
}

function sendLedCount(device, count, callback) {
    const report = new Uint8Array(64);
    report.set([0x52, 0x42, 0x07, 0x0e, 0x95, count]);
    report[6] = report.slice(0, 6).reduce((sum, value) => (sum + value) & 0xff, 0);
    let stream;
    try {
        stream = Gio.File.new_for_path(device).append_to(Gio.FileCreateFlags.NONE, null);
        const [written, count] = stream.write_all(report, null);
        stream.close(null);
        callback(written && count === 64 ? null : new Error(`wrote ${count} bytes instead of 64`));
    } catch (error) {
        callback(error);
    }
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
        const layout = loadLayout(devices);
        page.remove(loading);
        this._build(page, devices, layout);
    }

    _build(page, devices, layout) {
        const intro = new Adw.PreferencesGroup({
            title: 'DX-Light Configuration',
            description: 'Un bandeau par écran. Répartis les LEDs sur trois ou quatre côtés selon la pose réelle.',
        });
        intro.add(new Adw.ActionRow({
            title: `${devices.length} contrôleur(s) détecté(s)`,
            subtitle: 'Les réglages sont enregistrés localement et appliqués aux contrôleurs sélectionnés.',
        }));
        page.add(intro);

        const widgets = [];
        for (const [index, display] of layout.displays.entries())
            widgets.push(this._addDisplay(page, index, display, devices));
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
                device: devices[display.device.selected],
                screen: display.screen.selected === 0 ? 'left' : 'right',
                location: ['back', 'top', 'bottom', 'left', 'right'][display.location.selected],
                installation_direction: display.direction.selected === 0 ? 'left-to-right' : 'right-to-left',
                sync_area: display.syncArea.selected === 0 ? 'edge' : 'center',
                edge_count: display.edgeCount.selected + 3,
                zones: Object.fromEntries(EDGE_KEYS.map(key => [key, key === 'bottom' && display.edgeCount.selected === 0 ? 0 : display.zones[key].get_value_as_int()])),
            }));
            const savedLayout = {version: 2, displays, session: layout.session};
            try {
                if (!displays.length)
                    throw new Error('No controller is selected.');
                if (new Set(displays.map(display => display.device)).size !== displays.length)
                    throw new Error('Each display must use a different controller.');
                for (const display of displays) {
                    const count = Object.values(display.zones).reduce((total, value) => total + value, 0);
                    if (!display.device || count < 1 || count > 254)
                        throw new Error('Each strip must contain between 1 and 254 LEDs.');
                }
                saveLayout(savedLayout);
            } catch (error) {
                result.title = 'Enregistrement impossible';
                result.subtitle = error.message;
                return;
            }
            saveButton.sensitive = false;
            result.title = 'Enregistrement et application...';
            let remaining = displays.length;
            let failed = false;
            for (const display of displays) {
                const count = Object.values(display.zones).reduce((total, value) => total + value, 0);
                sendLedCount(display.device, count, error => {
                    if (failed)
                        return;
                    if (error) {
                        failed = true;
                        saveButton.sensitive = true;
                        result.title = 'Enregistrement impossible';
                        result.subtitle = error.message;
                        return;
                    }
                    remaining--;
                    if (remaining !== 0)
                        return;
                    saveButton.sensitive = true;
                    result.title = 'Configuration appliquée';
                    result.subtitle = 'Le total de chaque zone a été envoyé à son contrôleur.';
                });
            }
        });
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
