# Ambientika for Home Assistant

A comprehensive Home Assistant integration for Ambientika air quality management systems, providing full control and monitoring of your Ambientika devices with advanced zone management capabilities.

## Features

### Core Functionality
- **Device Control**: Complete control of Ambientika devices including fan speed, operating modes, humidity levels, and light sensor settings
- **Real-time Monitoring**: Live sensor data for temperature, humidity, air quality, and filter status
- **Zone Management**: Advanced multi-zone support with master/slave device relationships
- **Area/Floor Synchronization**: Automatic synchronization between Ambientika zones and Home Assistant areas/floors

### Platforms Supported

| Platform        | Description                                    | Features                                      |
| --------------- | ---------------------------------------------- | --------------------------------------------- |
| `sensor`        | Temperature, humidity, air quality monitoring  | Zone-aware master data consumption           |
| `binary_sensor` | Alarm states (night, humidity)                | Zone-based alarm management                   |
| `select`        | Device settings and zone master selection     | Only available for master devices            |
| `button`        | Filter reset functionality                     | Available for all devices                    |
| `switch`        | Management and synchronization controls       | Zone sync configuration                       |
| `management`    | Diagnostic and zone management sensors        | Comprehensive system monitoring               |

## Architecture

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Home Assistant                               │
│  ┌───────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │ Ambientika    │  │ Zone Sync       │  │ Management      │   │
│  │ Integration   │  │ Manager         │  │ Components      │   │
│  │               │  │                 │  │                 │   │
│  │ ┌───────────┐ │  │ ┌─────────────┐ │  │ ┌─────────────┐ │   │
│  │ │   Hub     │ │  │ │Area/Floor   │ │  │ │Diagnostics  │ │   │
│  │ │Coordinator│ │  │ │Sync         │ │  │ │& Controls   │ │   │
│  │ └───────────┘ │  │ └─────────────┘ │  │ └─────────────┘ │   │
│  │               │  │                 │  │                 │   │
│  │ ┌───────────┐ │  │ ┌─────────────┐ │  │ ┌─────────────┐ │   │
│  │ │Device     │ │  │ │Zone Master  │ │  │ │Zone Status  │ │   │
│  │ │Entities   │ │  │ │Selection    │ │  │ │Monitoring   │ │   │
│  │ └───────────┘ │  │ └─────────────┘ │  │ └─────────────┘ │   │
│  └───────────────┘  └─────────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Ambientika Cloud API                           │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │    Devices      │  │     Houses      │  │     Zones       │ │
│  │   Management    │  │   Management    │  │   Management    │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Zone-Aware Master Data Consumption

The integration implements sophisticated zone management where slave devices automatically consume master device data from their respective zones:

#### Zone Architecture
```
Zone 0 (Ground Floor)          Zone 1 (Upper Floor)
┌─────────────────────┐       ┌─────────────────────┐
│ Device A (Master)   │       │ Device C (Master)   │
│ - Controls zone     │       │ - Controls zone     │
│ - Sets parameters   │       │ - Sets parameters   │
│                     │       │                     │
│ Device B (Slave)    │       │ Device D (Slave)    │
│ - Shows A's data    │       │ - Shows C's data    │
│ - Follows A's mode  │       │ - Follows C's mode  │
└─────────────────────┘       └─────────────────────┘
```

#### Key Zone Features
1. **Master Device Control**: Only master devices expose select entities for configuration
2. **Slave Device Data**: Slave devices display their zone master's settings and status
3. **Zone Independence**: Each zone operates independently with its own master/slave hierarchy
4. **Dynamic Master Assignment**: Zone master roles can be changed through dedicated select entities

### API Integration

#### Device Role Management
The integration uses the official Ambientika API endpoint `/Device/apply-config` for managing device roles:

```python
# Zone role consistency ensures both rooms and zones arrays have matching roles
POST /Device/apply-config
{
  "id": "house_id",
  "name": "House Name",
  "rooms": [
    {
      "devices": [
        {"serialNumber": "xxx", "role": "Master"},
        {"serialNumber": "yyy", "role": "SlaveOppositeMaster"}
      ]
    }
  ],
  "zones": [
    {
      "rooms": [
        {
          "devices": [
            {"serialNumber": "xxx", "role": "Master"},
            {"serialNumber": "yyy", "role": "SlaveOppositeMaster"}
          ]
        }
      ]
    }
  ]
}
```

### Entity Structure

#### Core Device Entities (per device)
- **Sensors**: Temperature, Humidity, Air Quality, Filter Status
- **State Sensors**: Light Sensor Level, Fan Speed, Operating Mode, Humidity Level (zone-aware)
- **Binary Sensors**: Night Alarm, Humidity Alarm
- **Button**: Filter Reset

#### Zone-Specific Entities (master devices only)
- **Select Entities**: Light Sensor Level, Fan Speed, Operating Mode, Humidity Level
- **Zone Master Select**: Device role management per zone

#### Management Entities (integration-wide)
- **Management Sensors**: Sync Management, Zone Management, Zone Configuration Summary
- **Diagnostic Sensors**: Device Role, Zone Index, Configuration per device
- **Switches**: Sync Zones to Floors, Sync Rooms to Areas
- **Zone Sync Sensor**: Synchronization status and control

### Configuration Flow

The integration supports two-step configuration:

1. **Authentication**: Username/password for Ambientika account
2. **Zone Synchronization**: Configure how zones sync with Home Assistant
   - Sync Zones to Floors: Create HA floors for Ambientika zones
   - Sync Rooms to Areas: Create HA areas for Ambientika rooms

### Zone Synchronization

#### Bi-directional Sync Features
- **Floor Creation**: Ambientika zones → Home Assistant floors
- **Area Creation**: Ambientika rooms → Home Assistant areas  
- **Device Assignment**: Automatic device-to-area assignment
- **Conflict Resolution**: Handles naming conflicts and missing entities
- **Periodic Sync**: Automatic synchronization every 15 minutes

#### Sync Logic
```
Ambientika Structure        Home Assistant Structure
┌─────────────────┐        ┌─────────────────┐
│ House           │   →    │ Floor           │
│ ├── Zone 0      │   →    │ ├── Areas       │
│ │   ├── Room A  │   →    │ │   ├── Room A  │
│ │   └── Room B  │   →    │ │   └── Room B  │
│ └── Zone 1      │   →    │ └── Areas       │
│     ├── Room C  │   →    │     ├── Room C  │
│     └── Room D  │   →    │     └── Room D  │
└─────────────────┘        └─────────────────┘
```

## Installation

[![Open your Home Assistant instance and open the repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg?style=flat-square)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ambientika&repository=HomeAssistant-integration-for-Ambientika&category=integration)

### Manual Installation
1. Copy the `custom_components/ambientika` directory to your Home Assistant `config/custom_components/` folder
2. Restart Home Assistant
3. Add the "Ambientika for Home Assistant" integration via **Settings** > **Devices and Services** > **Integrations**

### HACS Installation
1. Add this repository to HACS custom repositories
2. Install the Ambientika integration
3. Restart Home Assistant
4. Configure the integration

## Configuration

Configuration is done through the Home Assistant UI:

### Initial Setup
1. **Credentials**: Enter your Ambientika account credentials
2. **Zone Sync**: Configure synchronization preferences
   - **Sync Zones to Floors**: Enable to create floors for zones (recommended for multi-zone setups)
   - **Sync Rooms to Areas**: Enable to create areas for rooms (recommended for organization)

### Management Controls
- **Zone Master Selection**: Change which device acts as master for each zone
- **Sync Controls**: Toggle switches for ongoing synchronization settings
- **Zone Status**: Monitor synchronization status and zone configuration

## Services

### Zone Synchronization Services
- `ambientika.sync_zones`: Manually trigger zone synchronization
- `ambientika.get_zone_status`: Get current zone synchronization status

### Service Parameters
```yaml
# Manual sync with options
service: ambientika.sync_zones
data:
  force_resync: true
  create_missing_floors: true
  create_missing_areas: true
```

## Advanced Features

### Zone Role Consistency
The integration implements zone role consistency to prevent API payload conflicts:
- Updates both `rooms` and `zones` arrays in house configuration
- Ensures device roles are synchronized across all API data structures
- Provides detailed debug logging for troubleshooting

### Enhanced Debugging
Comprehensive logging system with specific tags:
- `Zone role consistency`: Device role updates and validation
- `Zone:`: Zone initialization and master/slave assignments
- Device tracking with serial numbers and role changes

### Error Handling
- Graceful degradation when API endpoints are unavailable
- Fallback methods for device role updates
- Automatic retry logic with exponential backoff
- Comprehensive error reporting and recovery

## Troubleshooting

### Common Issues
1. **Zone Master Changes Not Working**: Check logs for "Zone role consistency" messages
2. **Devices Not Syncing**: Verify zone synchronization settings and run manual sync
3. **Missing Areas/Floors**: Enable sync options and trigger manual synchronization

### Debug Information
Enable debug logging by adding to `configuration.yaml`:
```yaml
logger:
  logs:
    custom_components.ambientika: debug
```

Look for these debug message patterns:
- `Zone role consistency:` - Device role update processes
- `Zone:` - Zone initialization and device assignments
- `Zone sync:` - Area/floor synchronization activities

## Contributing

Contributions are welcome! Please read the [Contribution Guidelines](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

### Development Setup
1. Clone the repository
2. Set up a development environment with Home Assistant
3. Install dependencies: `ambientika_py`, `returns`
4. Follow the testing guidelines in CONTRIBUTING.md

## Version History

### v1.2.0 (Current)
- **Zone-Aware Master Data Consumption**: Complete implementation
- **Zone Role Consistency**: API payload consistency fixes
- **Enhanced Zone Management**: Comprehensive zone master selection
- **Area/Floor Synchronization**: Bi-directional sync with Home Assistant
- **Management Platform**: Diagnostic and control entities
- **Enhanced Debugging**: Detailed logging and troubleshooting

### v1.1.0
- Basic zone support and device management
- Core sensor and select entities

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues and feature requests, please use the [GitHub Issues](https://github.com/ambientika/HomeAssistant-integration-for-Ambientika/issues) page.


