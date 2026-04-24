import QtQuick
import QtQuick.Controls
import QtLocation
import QtPositioning

Rectangle {
    id: root
    color: "#f7f9fc"
    property real sceneMinLat: 35.75
    property real sceneMinLon: 120.05
    property real sceneMaxLat: 35.95
    property real sceneMaxLon: 120.25
    property bool mapReadySent: false

    function fitSceneBounds() {
        if (!overviewBridge.onlineTileHost || sceneMinLat >= sceneMaxLat || sceneMinLon >= sceneMaxLon) {
            return;
        }
        map.visibleRegion = QtPositioning.rectangle(
            QtPositioning.coordinate(sceneMaxLat, sceneMinLon),
            QtPositioning.coordinate(sceneMinLat, sceneMaxLon)
        )
        overviewBridge.notifyMapState(map.center.latitude, map.center.longitude, map.zoomLevel)
    }

    Plugin {
        id: mapPlugin
        name: "osm"
        PluginParameter {
            name: "osm.mapping.custom.host"
            value: overviewBridge.onlineTileHost
        }
        PluginParameter {
            name: "osm.mapping.providersrepository.disabled"
            value: true
        }
    }

    Map {
        id: map
        anchors.fill: parent
        plugin: mapPlugin
        activeMapType: supportedMapTypes.length > 0 ? supportedMapTypes[0] : null
        center: QtPositioning.coordinate((root.sceneMinLat + root.sceneMaxLat) * 0.5, (root.sceneMinLon + root.sceneMaxLon) * 0.5)
        zoomLevel: 16
        minimumZoomLevel: overviewBridge.onlineMinZoom
        maximumZoomLevel: overviewBridge.onlineMaxZoom

        Component.onCompleted: {
            if (!root.mapReadySent) {
                root.mapReadySent = true
                overviewBridge.notifyMapReady()
                Qt.callLater(root.fitSceneBounds)
            }
        }

        onCenterChanged: {
            overviewBridge.notifyMapState(center.latitude, center.longitude, zoomLevel)
        }

        onZoomLevelChanged: {
            overviewBridge.notifyMapState(center.latitude, center.longitude, zoomLevel)
        }

        PinchHandler {
            id: pinch
            target: null
            onActiveChanged: if (active) {
                map.startCentroid = map.toCoordinate(pinch.centroid.position, false)
            }
            onScaleChanged: (delta) => {
                map.zoomLevel += Math.log2(delta)
                map.alignCoordinateToPoint(map.startCentroid, pinch.centroid.position)
            }
            grabPermissions: PointerHandler.TakeOverForbidden
        }

        WheelHandler {
            id: wheel
            acceptedDevices: Qt.platform.pluginName === "cocoa" || Qt.platform.pluginName === "wayland"
                             ? PointerDevice.Mouse | PointerDevice.TouchPad
                             : PointerDevice.Mouse
            rotationScale: 1 / 120
            property: "zoomLevel"
        }

        DragHandler {
            id: drag
            target: null
            onTranslationChanged: (delta) => map.pan(-delta.x, -delta.y)
        }

        TapHandler {
            acceptedButtons: Qt.LeftButton
            onTapped: (point) => overviewBridge.notifyMapTapped(point.position.x, point.position.y)
        }
    }
}
