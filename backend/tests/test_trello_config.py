from app.services.trello_config import board_configs, list_state_for_name


def test_board_mappings_are_loaded():
    boards = {board.alias: board for board in board_configs()}

    assert list_state_for_name(boards["ALPHA"], "TAREAS") == "pending"
    assert list_state_for_name(boards["ALPHA"], "Perpetuas") == "perpetual"
    assert list_state_for_name(boards["BETA"], "MARKETING Y COMUNICACION") == "ignored"
