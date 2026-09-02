import 'dart:convert';
import 'package:flutter/material.dart';
import '../../services/api_client.dart';

/// Module 2 — reads GET /api/predict-destination/{id}. Read-only, no DB writes,
/// safe to call whenever this screen opens (contract explicitly allows this,
/// unlike /api/risk or /api/search-area).
class DestinationPredictionScreen extends StatefulWidget {
  final int patientId;
  final String patientName;
  const DestinationPredictionScreen({
    super.key,
    required this.patientId,
    required this.patientName,
  });

  @override
  State<DestinationPredictionScreen> createState() => _DestinationPredictionScreenState();
}

class _DestinationPredictionScreenState extends State<DestinationPredictionScreen> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _result;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await apiGet('/api/predict-destination/${widget.patientId}');
      if (res.statusCode != 200) {
        setState(() => _error = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้');
        return;
      }
      setState(() => _result = jsonDecode(res.body) as Map<String, dynamic>);
    } catch (_) {
      setState(() => _error = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _statusMessage(String status) {
    switch (status) {
      case 'no_profile':
        return 'ยังไม่มีสถานที่ที่ปักไว้สำหรับผู้ป่วยคนนี้ — ปักหมุดก่อนเพื่อให้ทำนายได้';
      case 'no_location':
        return 'ยังไม่มีข้อมูลตำแหน่งพอจะทำนาย — รอสักครู่ให้แอปผู้ป่วยส่ง GPS เข้ามา';
      case 'unknown_current_place':
        return 'ตอนนี้ผู้ป่วยไม่ได้อยู่ใกล้สถานที่ที่รู้จักเลย — ถ้ากังวลว่าหลงทาง ให้ดูหน้า Track แทน';
      default:
        return 'ยังทำนายไม่ได้ตอนนี้';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('คาดการณ์ปลายทาง — ${widget.patientName}')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _buildResult(),
    );
  }

  Widget _buildResult() {
    final result = _result!;
    final status = result['status'] as String? ?? 'unavailable';

    if (status != 'ok') {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(_statusMessage(status), textAlign: TextAlign.center),
        ),
      );
    }

    final historyStatus = result['history_status'] as String?;
    final transitionsObserved = result['transitions_observed'] as int? ?? 0;
    final currentPlaceName = result['current_place_name'] as String?;
    final predictions = (result['predictions'] as List?) ?? [];

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (currentPlaceName != null)
            Text('ตอนนี้อยู่ที่: $currentPlaceName',
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            'อ้างอิงจากสถิติการย้ายที่ผ่านมา ไม่ใช่โมเดล AI ทำนาย (นับได้ $transitionsObserved ครั้งใน 30 วัน)',
            style: TextStyle(fontSize: 12, color: Colors.grey[600]),
          ),
          if (historyStatus == 'sparse') ...[
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: Colors.orange[50],
                borderRadius: BorderRadius.circular(8),
              ),
              child: const Text(
                'ยังเก็บข้อมูลอยู่ — ตัวเลขด้านล่างยังไม่น่าเชื่อถือเต็มที่',
                style: TextStyle(fontSize: 13),
              ),
            ),
          ],
          const SizedBox(height: 16),
          if (predictions.isEmpty)
            const Text('ยังไม่มีปลายทางที่คาดการณ์ได้')
          else
            ...predictions.map((p) {
              final placeName = p['place_name'] as String? ?? 'สถานที่ที่ไม่มีชื่อ';
              final probabilityPct = p['probability_pct'];
              final rank = p['rank'];
              return Card(
                child: ListTile(
                  leading: CircleAvatar(child: Text('$rank')),
                  title: Text(placeName),
                  trailing: historyStatus == 'none' || probabilityPct == null
                      ? null
                      : Text('$probabilityPct%'),
                ),
              );
            }),
        ],
      ),
    );
  }
}
