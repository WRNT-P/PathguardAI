import 'dart:async';
import 'dart:io';
import 'package:image_picker/image_picker.dart';
import 'package:flutter/material.dart';
import '../../services/location_service.dart';

class QuestionCard extends StatelessWidget{
  final String text;
  final String hintText;
  final ValueChanged<String>? onChanged;

  const QuestionCard({
    super.key,
    required this.text,
    required this.hintText,
    this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            text,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: Colors.black,
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            decoration: InputDecoration(
              border: const OutlineInputBorder(),
              hintText: hintText,
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
            ),
            onChanged: onChanged,
          ),
        ]
      )
    );
  }
}

class MultipleChoiceCard extends StatefulWidget {
  final String text;
  final List<String> options;
  final ValueChanged<String>? onSelected;

  const MultipleChoiceCard({
    super.key,
    required this.text,
    required this.options,
    this.onSelected,
  });

  @override
  State<MultipleChoiceCard> createState() => _MultipleChoiceCardState();
}

class _MultipleChoiceCardState extends State<MultipleChoiceCard> {
  String? _selectedOption;

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.text,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: Colors.black,
            ),
          ),
          const SizedBox(height: 8),
          RadioGroup<String>(
            groupValue: _selectedOption,
            onChanged: (String? value) {
              setState(() {
                _selectedOption = value;
              });
              if (widget.onSelected != null && value != null) {
                widget.onSelected!(value);
              }
            },
            child: Column(
              children: widget.options.map((option) {
                return RadioListTile<String>(
                  title: Text(option),
                  value: option,
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  visualDensity: const VisualDensity(horizontal: -4, vertical: -4),
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                );
              }).toList(),
            ),
          ),
        ],
      ),
    );
  }
}

enum _LinkParseStatus { idle, loading, success, error }

class SafePlaceLinkInput extends StatefulWidget {
  final String text;
  final ValueChanged<ParsedLocation?>? onChanged;

  const SafePlaceLinkInput({
    super.key,
    required this.text,
    this.onChanged,
  });

  @override
  State<SafePlaceLinkInput> createState() => _SafePlaceLinkInputState();
}

class _SafePlaceLinkInputState extends State<SafePlaceLinkInput> {
  final TextEditingController _controller = TextEditingController();
  _LinkParseStatus _status = _LinkParseStatus.idle;
  ParsedLocation? _result;
  Timer? _debounce;

  @override
  void dispose() {
    _controller.dispose();
    _debounce?.cancel();
    super.dispose();
  }

  void _handleChanged(String value) {
    // Wait for a short pause after the last keystroke/paste before parsing,
    // so we're not firing a network request on every character typed.
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 500), () => _handleSubmit(value));
  }

  Future<void> _handleSubmit(String value) async {
    if (value.trim().isEmpty) {
      setState(() {
        _status = _LinkParseStatus.idle;
        _result = null;
      });
      return;
    }

    setState(() => _status = _LinkParseStatus.loading);

    final parsed = await parseGoogleMapsLink(value);

    if (!mounted) return;
    setState(() {
      _result = parsed;
      _status = parsed != null ? _LinkParseStatus.success : _LinkParseStatus.error;
    });
    widget.onChanged?.call(parsed);
  }

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.text,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: Colors.black,
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _controller,
            decoration: InputDecoration(
              border: const OutlineInputBorder(),
              hintText: 'Paste Google Maps link here',
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
              suffixIcon: _status == _LinkParseStatus.loading
                  ? const Padding(
                      padding: EdgeInsets.all(12),
                      child: SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    )
                  : null,
            ),
            onChanged: _handleChanged,
          ),
          const SizedBox(height: 6),
          if (_status == _LinkParseStatus.success && _result != null)
            Text(
              'Location found: ${_result!.latitude.toStringAsFixed(5)}, ${_result!.longitude.toStringAsFixed(5)}',
              style: const TextStyle(fontSize: 12, color: Colors.green),
            ),
          if (_status == _LinkParseStatus.error)
            const Text(
              "Couldn't read a location from that link — check it's a Google Maps share link and try again.",
              style: TextStyle(fontSize: 12, color: Colors.red),
            ),
        ],
      ),
    );
  }
}

class ProfilePictureInput extends StatefulWidget {
  final ValueChanged<File?>? onChanged;

  const ProfilePictureInput({
    super.key,
    this.onChanged,
  });

  @override
  State<ProfilePictureInput> createState() => _ProfilePictureInputState();
}

class _ProfilePictureInputState extends State<ProfilePictureInput> {
  File? _image;
  Future<void> _pickImage() async {
    final XFile? picked = await ImagePicker().pickImage(source:ImageSource.gallery);

    if (picked == null) return;
    setState(() {
      _image = File(picked.path);
    });
    widget.onChanged?.call(_image);
  }

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: GestureDetector(
        onTap: _pickImage,
        child: CircleAvatar(
          radius: 50,
          backgroundColor: Colors.grey[200],
          backgroundImage: _image != null ? FileImage(_image!) : null,
          child: _image == null ? const Icon(Icons.person, size: 50, color: Colors.black54) : null,
        ),
      ),
    );
  }
}

enum _PlaceLinkStatus { idle, loading, success, error }

/// One row for an "other familiar place" — a name plus a Google Maps link.
/// Reports the combined {name, location} back via [onChanged] only once
/// both parts are filled in; reports null otherwise so incomplete rows are
/// dropped silently rather than submitted half-empty.
class FamiliarPlaceInput extends StatefulWidget {
  final int index;
  final ValueChanged<Map<String, dynamic>?> onChanged;
  final VoidCallback onRemove;

  const FamiliarPlaceInput({
    super.key,
    required this.index,
    required this.onChanged,
    required this.onRemove,
  });

  @override
  State<FamiliarPlaceInput> createState() => _FamiliarPlaceInputState();
}

class _FamiliarPlaceInputState extends State<FamiliarPlaceInput> {
  final TextEditingController _nameController = TextEditingController();
  final TextEditingController _linkController = TextEditingController();
  _PlaceLinkStatus _status = _PlaceLinkStatus.idle;
  ParsedLocation? _location;
  Timer? _debounce;

  @override
  void dispose() {
    _nameController.dispose();
    _linkController.dispose();
    _debounce?.cancel();
    super.dispose();
  }

  void _reportChange() {
    final name = _nameController.text.trim();
    if (name.isNotEmpty && _location != null) {
      widget.onChanged({'name': name, 'location': _location});
    } else {
      widget.onChanged(null);
    }
  }

  void _handleLinkChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 500), () => _handleLinkSubmit(value));
  }

  Future<void> _handleLinkSubmit(String value) async {
    if (value.trim().isEmpty) {
      setState(() {
        _status = _PlaceLinkStatus.idle;
        _location = null;
      });
      _reportChange();
      return;
    }

    setState(() => _status = _PlaceLinkStatus.loading);

    final parsed = await parseGoogleMapsLink(value);

    if (!mounted) return;
    setState(() {
      _location = parsed;
      _status = parsed != null ? _PlaceLinkStatus.success : _PlaceLinkStatus.error;
    });
    _reportChange();
  }

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Container(
          padding: const EdgeInsets.fromLTRB(14, 10, 8, 14),
          decoration: BoxDecoration(
            color: Colors.grey[50],
            border: Border.all(color: Colors.grey[300]!),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header row: place number on the left, remove control
              // pinned to the top-right so it reads as "remove this card"
              // rather than being squeezed against the name field.
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    'Place ${widget.index}',
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: Colors.grey[600],
                    ),
                  ),
                  Semantics(
                    button: true,
                    label: 'Remove place ${widget.index}',
                    child: InkWell(
                      borderRadius: BorderRadius.circular(20),
                      onTap: widget.onRemove,
                      child: const Padding(
                        padding: EdgeInsets.all(4),
                        child: Icon(Icons.close, color: Colors.red, size: 20),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 2),
              const SizedBox(height: 6),
              TextField(
                controller: _nameController,
                decoration: const InputDecoration(
                  border: OutlineInputBorder(),
                  hintText: 'Enter place name',
                  contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 14),
                ),
                onChanged: (_) => _reportChange(),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: _linkController,
                decoration: InputDecoration(
                  border: const OutlineInputBorder(),
                  hintText: 'Paste Google Maps link here',
                  contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
                  suffixIcon: _status == _PlaceLinkStatus.loading
                      ? const Padding(
                          padding: EdgeInsets.all(12),
                          child: SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        )
                      : null,
                ),
                onChanged: _handleLinkChanged,
              ),
              const SizedBox(height: 4),
              if (_status == _PlaceLinkStatus.success && _location != null)
                Text(
                  'Location found: ${_location!.latitude.toStringAsFixed(5)}, ${_location!.longitude.toStringAsFixed(5)}',
                  style: const TextStyle(fontSize: 12, color: Colors.green),
                ),
              if (_status == _PlaceLinkStatus.error)
                const Text(
                  "Couldn't read a location from that link.",
                  style: TextStyle(fontSize: 12, color: Colors.red),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class AddPatientScreen extends StatefulWidget {
  const AddPatientScreen({super.key});
  @override
  State<AddPatientScreen> createState() => _AddPatientScreenState();
}

class _AddPatientScreenState extends State<AddPatientScreen> {
  static const int _maxOtherPlaces = 3;

  File? _profileImage;
  String? _patientName;
  String? _alzheimerState;
  ParsedLocation? _home;

  int _nextPlaceRowId = 0;
  final List<int> _otherPlaceRowIds = [];
  final Map<int, Map<String, dynamic>?> _otherPlaceValues = {};

  void _addPlaceRow() {
    setState(() {
      _otherPlaceRowIds.add(_nextPlaceRowId++);
    });
  }

  void _removePlaceRow(int id) {
    setState(() {
      _otherPlaceRowIds.remove(id);
      _otherPlaceValues.remove(id);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Stack(
          children: [
            Center(
              child: SingleChildScrollView(
                child: Column(
                  children: [
                    
                    ProfilePictureInput(
                      onChanged: (file) => setState(() => _profileImage = file),
                    ),
                    const SizedBox(height: 26),

                    QuestionCard(
                      text: '1. What is your patient name? ',
                      hintText: 'eg. Robert',
                      onChanged: (value) => setState(() => _patientName = value),
                    ),
                    const SizedBox(height: 16),

                    MultipleChoiceCard(
                      text: '2. What is the patient state of Alzheimer\'s?',
                      options: const ['1 : Normal-Memory Loss', '2 : Memory Loss-Severe'],
                      onSelected: (value) => setState(() => _alzheimerState = value),
                    ),
                    const SizedBox(height: 16),

                    SafePlaceLinkInput(
                      text: '3. Where is your patient\'s home?',
                      onChanged: (value) => setState(() => _home = value),
                    ),

                    const SizedBox(height: 20),
                    FractionallySizedBox(
                      widthFactor: 0.7,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            '4. Other places your patient knows well (recommended)',
                            style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: Colors.black),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            'e.g. temple, market, a relative\'s house — helps avoid false alarms when your patient visits places they know.',
                            style: TextStyle(fontSize: 12, color: Colors.grey[700]),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),

                    for (int i = 0; i < _otherPlaceRowIds.length; i++)
                      FamiliarPlaceInput(
                        key: ValueKey(_otherPlaceRowIds[i]),
                        index: i + 1,
                        onChanged: (value) => _otherPlaceValues[_otherPlaceRowIds[i]] = value,
                        onRemove: () => _removePlaceRow(_otherPlaceRowIds[i]),
                      ),

                    if (_otherPlaceRowIds.length < _maxOtherPlaces)
                      FractionallySizedBox(
                        widthFactor: 0.7,
                        child: Align(
                          alignment: Alignment.centerLeft,
                          child: OutlinedButton.icon(
                            onPressed: _addPlaceRow,
                            icon: const Icon(Icons.add, size: 18),
                            label: Text('Add a place (${_otherPlaceRowIds.length}/$_maxOtherPlaces)'),
                            style: OutlinedButton.styleFrom(
                              foregroundColor: Colors.blue,
                              side: BorderSide(color: Colors.blue[200]!),
                              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(8),
                              ),
                            ),
                          ),
                        ),
                      ),

                    const SizedBox(height: 16),
                    ElevatedButton(
                      onPressed: () {
                        if (_patientName == null || _patientName!.isEmpty) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(content: Text('Please enter a patient name')),
                          );
                        }
                        else if(_alzheimerState == null){
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(content: Text('Please select an Alzheimer\'s state')),
                          );
                        }
                        else if(_home == null){
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(content: Text('Please provide the home location')),
                          );
                        }
                        else{
                          final otherPlaces = _otherPlaceValues.values
                              .where((value) => value != null)
                              .toList();
                          Navigator.pop(context,{
                            'name': _patientName,
                            'state': _alzheimerState,
                            'home': _home,
                            'otherPlaces': otherPlaces,
                            'profileImage': _profileImage,
                          });
                        }
                      },
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.blue,
                        minimumSize: const Size(0, 48),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(10),
                        ),
                      ),
                      child: const Text('Confirm', style: TextStyle(color: Colors.white, fontSize: 14)),
                    ),
                  ]
                )
              )
            ),
            Positioned(
              top: 16,
              left: 16,
              child: Container(
                decoration: BoxDecoration(
                  color: Colors.grey[200],
                  shape: BoxShape.circle,
                ),
                child: IconButton(
                  icon: const Icon(
                    Icons.arrow_back,
                    color: Colors.black,
                    size: 20,
                  ),
                  onPressed: () {
                    Navigator.pop(context);
                  }
                )
              )
            ),
          ]
        )
      ),
    );
  }
}