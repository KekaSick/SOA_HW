-- Только учебные учётные записи. Пароль каждой: demo-password.
INSERT INTO users (username, password_hash, role) VALUES
('user', '$2a$10$mP.HmT1asMj7Grppa9mHwujfHl2LPDa.6u7Ss9v8Ds4IoVxKMzfgW', 'USER'),
('seller', '$2a$10$mP.HmT1asMj7Grppa9mHwujfHl2LPDa.6u7Ss9v8Ds4IoVxKMzfgW', 'SELLER'),
('seller2', '$2a$10$mP.HmT1asMj7Grppa9mHwujfHl2LPDa.6u7Ss9v8Ds4IoVxKMzfgW', 'SELLER'),
('admin', '$2a$10$mP.HmT1asMj7Grppa9mHwujfHl2LPDa.6u7Ss9v8Ds4IoVxKMzfgW', 'ADMIN');
INSERT INTO products (name, description, price, stock, category, status, seller_id) VALUES
('Клавиатура', 'Учебный товар', 2500.00, 10, 'electronics', 'ACTIVE', 2),
('Мышь', NULL, 1000.00, 20, 'electronics', 'ACTIVE', 2),
('Блокнот', NULL, 200.00, 0, 'stationery', 'INACTIVE', 3);
INSERT INTO promo_codes (code, discount_type, discount_value, min_order_amount, max_uses, valid_from, valid_until) VALUES
('DEMO20', 'PERCENTAGE', 20, 1000, 100, CURRENT_TIMESTAMP - INTERVAL '1 day', CURRENT_TIMESTAMP + INTERVAL '30 days'),
('FIXED500', 'FIXED_AMOUNT', 500, 1000, 100, CURRENT_TIMESTAMP - INTERVAL '1 day', CURRENT_TIMESTAMP + INTERVAL '30 days');
