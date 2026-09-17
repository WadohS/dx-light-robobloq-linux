import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const API_URL = 'http://127.0.0.1:8000';
const EDGE_KEYS = ['left', 'top', 'right', 'bottom'];
const SESSION_EFFECTS = [
    ['dxlight-dynamix', 'Dynamix'], ['dxlight-serpentin', 'Serpentin'], ['dxlight-feu', 'Feu'],
    ['dxlight-4', 'Météore'], ['dxlight-5', 'Scintillement'], ['dxlight-6', 'Dégradé'], ['dxlight-7', 'Défilement'],
    ...Array.from({length: 7}, (_value, index) => [`dxlight-rhythm-${index}`, `Rythme ${index + 1}`]),
];

function request(method, path, body, callback) {
    const args = ['curl', '--fail', '--silent', '--show-error', '--request', method, `${API_URL}${path}`];
    if (body !== null)
        args.push('--header', 'Content-Type: application/json', '--data', JSON.stringify(body));

    const process = Gio.Subprocess.new(args, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    process.communicate_utf8_async(null, null, (source, result) => {
        try {
            const [, output] = source.communicate_utf8_finish(result);
            callback(null, JSON.parse(output));
        } catch (error) {
            callback(error, null);
        }
    });
}

function deviceLabel(path) {
    const match = path.match(/usb-[^:]+:(.+)-hidraw$/);
    return match ? `Contrôleur USB ${match[1]}` : path;
}

function dropdown(labels, selected) {
    const widget = Gtk.DropDown.new_from_strings(labels);
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

        request('GET', '/api/devices', null, (deviceError, deviceResponse) => {
            if (deviceError) {
                loading.remove(loading.get_first_child());
                loading.add(new Adw.ActionRow({title: 'Service LED indisponible', subtitle: 'Démarre robobloq-led.service puis rouvre cette fenêtre.'}));
                return;
            }
            request('GET', '/api/layout', null, (layoutError, layoutResponse) => {
                if (layoutError) {
                    loading.remove(loading.get_first_child());
                    loading.add(new Adw.ActionRow({title: 'Configuration indisponible'}));
                    return;
                }
                page.remove(loading);
                this._build(page, deviceResponse.devices, layoutResponse.layout);
            });
        });
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
        const session = this._addSession(page, layout.session);

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
            saveButton.sensitive = false;
            result.title = 'Enregistrement et application...';
            request('PUT', '/api/layout', {version: 2, displays, session: this._sessionPayload(session)}, (error) => {
                saveButton.sensitive = true;
                result.title = error ? 'Enregistrement impossible' : 'Configuration appliquée';
                result.subtitle = error ? error.message : 'Le total de chaque zone a été envoyé à son contrôleur.';
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
