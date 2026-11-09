import 'package:flutter/material.dart';

import 'api/nota_client.dart';
import 'pages/home_page.dart';

/// 后端地址在构建时注入，默认指向本机：
///   flutter run --dart-define=NOTA_BASE=http://10.0.2.2:8787   # Android 模拟器
///   flutter run --dart-define=NOTA_BASE=http://127.0.0.1:8787  # 桌面 / Web
const _base = String.fromEnvironment('NOTA_BASE', defaultValue: 'http://127.0.0.1:8787');

void main() {
  runApp(const NotaApp());
}

class NotaApp extends StatelessWidget {
  const NotaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Nota',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF6EE7B7),
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF0F1115),
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFF101318),
          elevation: 0,
        ),
        cardTheme: const CardThemeData(
          color: Color(0xFF171A21),
          elevation: 0,
          margin: EdgeInsets.symmetric(vertical: 6),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0xFF1D212A),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: const BorderSide(color: Color(0xFF2A2F3A)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: const BorderSide(color: Color(0xFF2A2F3A)),
          ),
        ),
      ),
      home: HomePage(client: NotaClient(baseUrl: _base)),
    );
  }
}
